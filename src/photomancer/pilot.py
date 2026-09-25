"""Pilot protocol (gates G2-G4) on a COPY of a sample of the archive.

The pilot lives in <data_dir>/pilot:
    images/       copied sample (originals never touched)
    work/         the pilot's own catalog.db and cache
    sample.csv    which files were copied
    ground_truth_template.csv, vocabulary.txt, labelling_sheet.html
    *_report.*    A/B, benchmark, evaluation and repeatability reports

Commands (python -m photomancer pilot <cmd>):
    sample    stratified random sample across top-level folders, copied
    run       tag the whole pilot with the active configuration (+ report)
    ab        G2: 768 vs 1024 px x with/without EXIF context on N photos
    bench     G2: concurrency 1 / 2 / 4 - throughput, latency, VRAM peak
    template  G3: ground-truth CSV to fill in by hand
    eval      G3: accuracy, macro-F1, confusion matrix, per-class recall, worst errors
    repeat    G3: same N photos twice - label agreement
"""

from __future__ import annotations

import copy
import csv
import html
import json
import logging
import os
import random
import shutil
import threading
from collections import defaultdict
from datetime import datetime

from . import lmstudio
from .classify import active_config
from .config import Settings
from .db import Catalog
from .pipeline import classify_shas, ensure_server, run as run_pipeline
from .report import CSS, thumb_data_url
from .scan import iter_jpegs, long_path
from .taxonomy import load_taxonomy

log = logging.getLogger("photomancer.pilot")


def pilot_settings(settings: Settings, **vlm_over) -> Settings:
    """Settings pointing at the pilot copy (optionally with VLM overrides)."""
    raw = copy.deepcopy(settings.raw)
    raw["photo_root"] = os.path.join(settings.pilot_dir, "images")
    raw["data_dir"] = os.path.join(settings.pilot_dir, "work")
    raw["vlm"].update(vlm_over)
    raw["dedup"]["propagate"] = False
    s = Settings(raw)
    s.photo_root, s.data_dir = raw["photo_root"], raw["data_dir"]   # ignore env overrides
    s.derive_paths()
    s.report_dir, s.pilot_dir = settings.pilot_dir, settings.pilot_dir
    s.ensure_dirs()
    return s


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


# ------------------------------------------------------------------ sample --
def make_sample(settings: Settings, n: int = 100, seed: int = 42, replace: bool = False) -> int:
    """Copy a stratified random sample (by top-level folder) into pilot/images."""
    dest = os.path.join(settings.pilot_dir, "images")
    if os.path.isdir(dest) and os.listdir(dest) and not replace:
        log.info("ERROR the pilot already has images in %s - use --replace to start over", dest)
        return 1
    log.info("=== choose pilot sample ===")
    files = [p for p, _s, _m in iter_jpegs(settings.photo_root, settings.exclude_dirs)]
    strata: dict[str, list[str]] = defaultdict(list)
    for p in files:
        strata[p.split(os.sep)[0] if os.sep in p else "(root)"].append(p)
    rng = random.Random(seed)
    total = len(files)
    quota = {k: max(1, n * len(v) // total) for k, v in strata.items()}
    rest = n - sum(quota.values())
    for k in sorted(strata, key=lambda k: -(n * len(strata[k]) / total - quota[k]))[:max(0, rest)]:
        quota[k] += 1
    picked = []
    for k in sorted(strata):
        picked += rng.sample(sorted(strata[k]), min(quota[k], len(strata[k])))
    if replace and os.path.isdir(settings.pilot_dir):
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(os.path.join(settings.pilot_dir, "work"), ignore_errors=True)
    log.info("=== copy pilot images ===")
    for i, rel in enumerate(sorted(picked), 1):
        out = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(long_path(os.path.join(settings.photo_root, rel)), long_path(out))
        if i % 10 == 0 or i == len(picked):
            log.info("copied %d/%d", i, len(picked))
    with open(os.path.join(settings.pilot_dir, "sample.csv"), "w", newline="",
              encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "stratum"])
        for rel in sorted(picked):
            w.writerow([rel, rel.split(os.sep)[0]])
    log.info("pilot sample: %d photos from %d folders (seed %d) in %s",
             len(picked), len(strata), seed, dest)
    for k in sorted(strata):
        log.info("  %-24s %3d of %d", k, quota[k], len(strata[k]))
    return 0


def run_pilot(settings: Settings, **kw) -> int:
    ps = pilot_settings(settings)
    if not os.path.isdir(ps.photo_root):
        log.info("ERROR no pilot sample yet - run `pilot sample` first")
        return 1
    return run_pipeline(ps, do_report=True, **kw)


# ------------------------------------------------------------- helpers --
def _items(ps: Settings, cat: Catalog, n: int, seed: int) -> list[tuple[str, str]]:
    rows = cat.q("SELECT f.sha256, MIN(f.path) AS path FROM files f JOIN stage_status s "
                 "ON s.file_id=f.file_id AND s.stage='prepare' AND s.status='ok' "
                 "WHERE f.missing=0 GROUP BY f.sha256 ORDER BY path")
    items = [(r["sha256"], r["path"]) for r in rows]
    return sorted(random.Random(seed).sample(items, min(n, len(items))), key=lambda x: x[1])


def _ready(settings: Settings) -> tuple[Settings, Catalog, object] | None:
    """Pilot settings + catalog with every pilot photo prepared."""
    ps = pilot_settings(settings)
    if not os.path.isdir(ps.photo_root):
        log.info("ERROR no pilot sample yet - run `pilot sample` first")
        return None
    run_pipeline(ps, prepare_only=True)
    return ps, Catalog(ps.db_path), load_taxonomy(ps.taxonomy_path)


def _results(cat: Catalog, key: str, shas: list[str]) -> dict[str, dict]:
    out = {}
    for sha in shas:
        r = cat.current_result(sha, key)
        if r is not None:
            out[sha] = {"data": json.loads(r["json"]), "latency": r["latency_s"],
                        "tin": r["tokens_in"], "tout": r["tokens_out"]}
    return out


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")


FIELDS_AGREE = [("primary_genre", lambda d: d["primary_genre"]),
                ("subgenre", lambda d: d["subgenre"]),
                ("people.present", lambda d: d["people"]["present"]),
                ("time_of_day", lambda d: d["time_of_day"]),
                ("setting", lambda d: d["setting"]),
                ("season", lambda d: d["season"])]


def _agreement(a: dict, b: dict) -> dict[str, tuple[int, int]]:
    both = [s for s in a if s in b]
    return {name: (sum(f(a[s]["data"]) == f(b[s]["data"]) for s in both), len(both))
            for name, f in FIELDS_AGREE}


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    log.info("Report written: %s", path)


# ------------------------------------------------------------------- G2 --
def ab_test(settings: Settings, n: int = 30, seed: int = 42, concurrency: int = 1) -> int:
    """768 vs 1024 px and with vs without EXIF context, same photos."""
    ready = _ready(settings)
    if not ready:
        return 1
    ps, cat, tax = ready
    items = _items(ps, cat, n, seed)
    ensure_server(ps, True)
    configs = [(1024, True), (1024, False), (768, True), (768, False)]
    res, keys = {}, {}
    for side, exif in configs:
        s2 = pilot_settings(settings, image_side=side, exif_context=exif)
        cfg = active_config(s2, tax)
        keys[(side, exif)] = cfg["key"]
        todo = [(sha, p) for sha, p in items if cat.current_result(sha, cfg["key"]) is None]
        log.info("=== tag A/B config %d px, EXIF %s (%d photos) ===", side, "on" if exif else "off",
                 len(todo))
        if todo:
            classify_shas(s2, cat, tax, todo, cfg, f"ab-{side}-{int(exif)}", concurrency)
        res[(side, exif)] = _results(cat, cfg["key"], [s for s, _ in items])
    ref = res[(1024, True)]
    L = [f"# G2 - configuration A/B ({_stamp()})", "",
         f"{len(items)} pilot photos, concurrency {concurrency}, model {ps.lm['model']}.",
         "Latency = one request end to end (includes repair retries).", "",
         "| config | ok | latency p50 | p95 | tokens in | tokens out |", "|---|---|---|---|---|---|"]
    for c in configs:
        r = res[c]
        lat = [v["latency"] for v in r.values()]
        tin = sum(v["tin"] for v in r.values()) / max(1, len(r))
        tout = sum(v["tout"] for v in r.values()) / max(1, len(r))
        L.append(f"| {c[0]} px, EXIF {'on' if c[1] else 'off'} | {len(r)}/{len(items)} | "
                 f"{_pct(lat, .5):.1f} s | {_pct(lat, .95):.1f} s | {tin:.0f} | {tout:.0f} |")
    L += ["", "## Agreement with 1024 px + EXIF", "",
          "| config | " + " | ".join(n for n, _ in FIELDS_AGREE) + " |",
          "|---|" + "---|" * len(FIELDS_AGREE)]
    for c in configs[1:]:
        ag = _agreement(ref, res[c])
        L.append(f"| {c[0]} px, EXIF {'on' if c[1] else 'off'} | " + " | ".join(
            f"{a}/{t} ({a / t:.0%})" if t else "-" for a, t in ag.values()) + " |")
    L += ["", "## Photos where primary_genre differs between configurations", ""]
    for sha, path in items:
        vals = {c: res[c].get(sha, {}).get("data", {}).get("primary_genre", "-") for c in configs}
        if len(set(vals.values())) > 1:
            L.append(f"- `{path}`: " + ", ".join(f"{c[0]}/{'exif' if c[1] else 'noexif'}={v}"
                                                   for c, v in vals.items()))
    text = "\n".join(L) + "\n"
    log.info("\n%s", text)
    _write(os.path.join(ps.pilot_dir, f"ab_report_{_stamp()}.md"), text)
    return 0


def bench(settings: Settings, n: int = 30, seed: int = 42, levels=(1, 2, 4)) -> int:
    """Throughput and VRAM at several concurrency levels (fresh requests each time)."""
    ready = _ready(settings)
    if not ready:
        return 1
    ps, cat, tax = ready
    items = _items(ps, cat, n, seed)
    ts = _stamp()
    rows = []
    for c in levels:
        ensure_server(ps, True, c)                 # reload with c parallel slots
        cfg = active_config(ps, tax, tag=f"bench-{ts}-c{c}")
        peak = [lmstudio.gpu_memory_mb() or 0]
        stop = threading.Event()

        def poll() -> None:
            while not stop.is_set():
                peak[0] = max(peak[0], lmstudio.gpu_memory_mb() or 0)
                stop.wait(1.0)

        th = threading.Thread(target=poll, daemon=True)
        th.start()
        log.info("=== benchmark concurrency %d (%d photos) ===", c, len(items))
        st = classify_shas(ps, cat, tax, items, cfg, f"bench-c{c}", c)
        stop.set()
        th.join()
        rows.append((c, st, peak[0]))
    total = Catalog(settings.db_path).one("SELECT COUNT(*) FROM files WHERE missing=0") or 0
    if not total:                                  # archive not scanned yet: count on disk
        total = sum(1 for _ in iter_jpegs(settings.photo_root, settings.exclude_dirs))
    cache_bytes = sum(os.path.getsize(os.path.join(dp, f)) for dp, _d, fs in
                      os.walk(os.path.join(ps.data_dir, "cache")) for f in fs)
    n_prep = cat.one("SELECT COUNT(*) FROM stage_status WHERE stage='prepare' AND status='ok'") or 1
    L = [f"# G2 - concurrency benchmark ({ts})", "",
         f"{len(items)} pilot photos per level, {ps.vlm['image_side']} px, EXIF context "
         f"{'on' if ps.vlm['exif_context'] else 'off'}, model {ps.lm['model']}.", "",
         "| concurrency | ok | failed | photos/min | latency p50 | p95 | VRAM peak |",
         "|---|---|---|---|---|---|---|"]
    for c, st, vram in rows:
        L.append(f"| {c} | {st['ok']} | {st['failed']} | {st['per_min']:.1f} | {st['p50']:.1f} s | "
                 f"{st['p95']:.1f} s | {vram} MB |")
    clean = [r for r in rows if not r[1]["failed"]] or rows
    best = max(clean, key=lambda r: r[1]["per_min"])
    L += ["", "## G4 - full-run estimate (from these measurements)", "",
          f"- JPEGs in the archive: {total}",
          f"- best measured without failures: concurrency {best[0]} at "
          f"{best[1]['per_min']:.1f} photos/min (context {ps.lm['context_per_request']} "
          f"tokens per request)",
          (f"- GPU time for the whole archive: about {total / best[1]['per_min'] / 60:.1f} h "
           f"(plus preparation, which overlaps)" if total and best[1]['per_min'] else "- n/a"),
          f"- image cache: {cache_bytes / max(1, n_prep) / 1024:.0f} KB per photo -> about "
          f"{cache_bytes / max(1, n_prep) * total / 1e9:.1f} GB for the archive"]
    text = "\n".join(L) + "\n"
    log.info("\n%s", text)
    _write(os.path.join(ps.pilot_dir, f"bench_report_{ts}.md"), text)
    return 0


# ------------------------------------------------------------------- G3 --
def template(settings: Settings) -> int:
    """Ground-truth CSV (blank, to fill in), vocabulary list and a labelling sheet."""
    ready = _ready(settings)
    if not ready:
        return 1
    ps, cat, tax = ready
    rows = cat.q("SELECT path, sha256 FROM files WHERE missing=0 ORDER BY path")
    p = os.path.join(ps.pilot_dir, "ground_truth_template.csv")
    if os.path.exists(p):
        p = os.path.join(ps.pilot_dir, f"ground_truth_template_{_stamp()}.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "primary_genre", "subgenre", "people_present", "notes"])
        for r in rows:
            w.writerow([r["path"], "", "", "", ""])
    voc = ["Fill primary_genre and subgenre with these exact values; people_present = yes / no.", ""]
    for g, v in tax.genres.items():
        voc += [f"{g}: {v['description']}", "    " + ", ".join(v["subgenres"]), ""]
    with open(os.path.join(ps.pilot_dir, "vocabulary.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(voc))
    cards = "".join(f'<div class="card"><img src="{thumb_data_url(ps, r["sha256"])}">'
                    f'<div>{html.escape(r["path"])}</div></div>' for r in rows)
    _write(os.path.join(ps.pilot_dir, "labelling_sheet.html"),
           f"<!doctype html><meta charset='utf-8'><title>Pilot labelling sheet</title>"
           f"<style>{CSS}</style><h1>Pilot photos ({len(rows)})</h1><div class='grid'>{cards}</div>")
    log.info("Ground-truth template: %s (%d rows) - fill it in, then run `pilot eval`", p, len(rows))
    return 0


def _read_gt(path: str) -> dict[str, dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    out = {}
    for r in csv.DictReader(text.splitlines(), dialect=dialect):
        g = (r.get("primary_genre") or "").strip().lower()
        if not g:
            continue
        pp = (r.get("people_present") or "").strip().lower()
        out[os.path.normpath(r["file"].strip())] = {
            "primary_genre": g, "subgenre": (r.get("subgenre") or "").strip().lower(),
            "people_present": (True if pp in ("yes", "y", "true", "1", "si", "sì") else
                               False if pp in ("no", "n", "false", "0") else None)}
    return out


def evaluate(settings: Settings, gt_path: str) -> int:
    """Compare the pilot's current results with a filled-in ground-truth CSV."""
    from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, recall_score
    ps = pilot_settings(settings)
    cat, tax = Catalog(ps.db_path), load_taxonomy(ps.taxonomy_path)
    cfg = active_config(ps, tax)
    gt = _read_gt(gt_path)
    unknown = sorted({v["primary_genre"] for v in gt.values()} - set(tax.genre_names))
    if unknown:
        log.info("WARNING ground truth uses genres not in the taxonomy: %s", ", ".join(unknown))
    pairs = []
    for f in cat.q("SELECT path, sha256 FROM files WHERE missing=0 ORDER BY path"):
        g = gt.get(os.path.normpath(f["path"]))
        r = cat.current_result(f["sha256"], cfg["key"]) if g else None
        if g and r is not None:
            pairs.append((f["path"], f["sha256"], g, json.loads(r["json"])))
    if not pairs:
        log.info("ERROR no photo has both a ground-truth label and a result for config %s "
                 "(tag the pilot first: `pilot run`)", cfg["key"])
        return 1
    y = [g["primary_genre"] for _p, _s, g, _d in pairs]
    yhat = [d["primary_genre"] for _p, _s, _g, d in pairs]
    labels = sorted(set(y) | set(yhat))
    acc = accuracy_score(y, yhat)
    f1 = f1_score(y, yhat, labels=labels, average="macro", zero_division=0)
    rec = recall_score(y, yhat, labels=labels, average=None, zero_division=0)
    cm = confusion_matrix(y, yhat, labels=labels)
    sub = [(g["subgenre"], d["subgenre"]) for _p, _s, g, d in pairs if g["subgenre"]]
    ppl = [(g["people_present"], d["people"]["present"]) for _p, _s, g, d in pairs
           if g["people_present"] is not None]
    wrong = sorted([x for x in pairs if x[2]["primary_genre"] != x[3]["primary_genre"]],
                   key=lambda x: -x[3]["genre_confidence"])
    log.info("=== G3 evaluation ===")
    log.info("photos evaluated: %d (config %s)", len(pairs), cfg["key"])
    log.info("primary_genre accuracy %.1f%%, macro-F1 %.3f", acc * 100, f1)
    if sub:
        log.info("subgenre accuracy %.1f%% (%d labelled)",
                 100 * sum(a == b for a, b in sub) / len(sub), len(sub))
    if ppl:
        log.info("people.present accuracy %.1f%% (%d labelled)",
                 100 * sum(a == b for a, b in ppl) / len(ppl), len(ppl))
    for lab, r in zip(labels, rec):
        log.info("  recall %-24s %.2f  (n=%d)", lab, r, y.count(lab))
    head = "".join(f"<th>{html.escape(l[:10])}</th>" for l in labels)
    body = "".join(f"<tr><th>{html.escape(l)}</th>" + "".join(
        f"<td style='background:rgba(37,150,140,{min(1, v / max(1, cm[i].sum())):.2f})'>{v or ''}</td>"
        for v in cm[i]) + "</tr>" for i, l in enumerate(labels))
    worst = "".join(
        f'<div class="card"><img src="{thumb_data_url(ps, s)}"><div>truth <b>{html.escape(g["primary_genre"])}'
        f'</b>/{html.escape(g["subgenre"])}</div><div>model <b>{html.escape(d["primary_genre"])}</b>/'
        f'{html.escape(d["subgenre"])} {d["genre_confidence"]:.2f}</div><div>{html.escape(d["caption"])}</div>'
        f'<div class="muted">{html.escape(p)}</div></div>' for p, s, g, d in wrong[:10])
    recs = "".join(f"<tr><td>{html.escape(l)}</td><td>{r:.2f}</td><td>{y.count(l)}</td></tr>"
                   for l, r in zip(labels, rec))
    _write(os.path.join(ps.pilot_dir, f"eval_report_{_stamp()}.html"),
           f"<!doctype html><meta charset='utf-8'><title>Pilot evaluation</title><style>{CSS}"
           f"</style><h1>G3 - pilot evaluation</h1><p class='muted'>config {cfg['key']} "
           f"{html.escape(json.dumps(cfg['ident']))}<br>ground truth {html.escape(gt_path)}</p>"
           f"<div><span class='kpi'><b>{len(pairs)}</b>photos</span><span class='kpi'><b>"
           f"{acc:.1%}</b>genre accuracy</span><span class='kpi'><b>{f1:.3f}</b>macro-F1</span>"
           + (f"<span class='kpi'><b>{sum(a == b for a, b in sub) / len(sub):.1%}</b>subgenre</span>"
              if sub else "")
           + (f"<span class='kpi'><b>{sum(a == b for a, b in ppl) / len(ppl):.1%}</b>people present"
              f"</span>" if ppl else "")
           + f"</div><h2>Confusion matrix (rows = truth, columns = model)</h2><table><tr><th></th>"
           f"{head}</tr>{body}</table><h2>Per-class recall</h2><table><tr><th>genre</th>"
           f"<th>recall</th><th>n</th></tr>{recs}</table><h2>10 worst errors (wrong genre, "
           f"highest confidence first)</h2><div class='grid'>{worst}</div>")
    return 0


def repeat(settings: Settings, n: int = 30, seed: int = 42, concurrency: int = 2) -> int:
    """Classify the same photos twice with fresh requests; report label agreement."""
    ready = _ready(settings)
    if not ready:
        return 1
    ps, cat, tax = ready
    items = _items(ps, cat, n, seed)
    ensure_server(ps, True)
    ts = _stamp()
    res = []
    for tag in ("a", "b"):
        cfg = active_config(ps, tax, tag=f"repeat-{ts}-{tag}")
        log.info("=== repeatability pass %s (%d photos) ===", tag.upper(), len(items))
        classify_shas(ps, cat, tax, items, cfg, f"repeat-{tag}", concurrency)
        res.append(_results(cat, cfg["key"], [s for s, _ in items]))
    ag = _agreement(res[0], res[1])
    L = [f"# G3 - repeatability ({ts})", "",
         f"Same {len(items)} photos classified twice (temperature {ps.vlm['temperature']}, "
         f"{ps.vlm['image_side']} px, EXIF {'on' if ps.vlm['exif_context'] else 'off'}).", "",
         "| field | agreement |", "|---|---|"]
    L += [f"| {k} | {a}/{t} ({a / t:.0%}) |" if t else f"| {k} | - |" for k, (a, t) in ag.items()]
    L += ["", "## Photos whose primary_genre or subgenre changed", ""]
    for sha, path in items:
        a, b = res[0].get(sha), res[1].get(sha)
        if a and b and (a["data"]["primary_genre"], a["data"]["subgenre"]) != (
                b["data"]["primary_genre"], b["data"]["subgenre"]):
            L.append(f"- `{path}`: {a['data']['primary_genre']}/{a['data']['subgenre']} -> "
                     f"{b['data']['primary_genre']}/{b['data']['subgenre']}")
    text = "\n".join(L) + "\n"
    log.info("\n%s", text)
    _write(os.path.join(ps.pilot_dir, f"repeat_report_{ts}.md"), text)
    return 0

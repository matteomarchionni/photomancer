"""Stage 6 - report: statistics, EXIF/VLM disagreements, CSV of the full
catalog and an HTML contact sheet (random sample per genre) for review.
Every number comes from the database; nothing is estimated.
"""

from __future__ import annotations

import base64
import csv
import html
import json
import logging
import os
import random
import re
from collections import Counter
from datetime import datetime

from .config import Settings
from .db import Catalog
from .xmp_writer import build_tags, effective_results

log = logging.getLogger("photomancer.report")

PEOPLE_GENRES = {"portrait", "street", "sports_action", "events_celebrations"}

ALL_YEARS, NO_DATE = "All years", "No date"


def photo_year(exif: dict | None) -> str:
    """Year the photo was taken, from EXIF DateTimeOriginal ('' when absent or bogus).

    File metadata only, never the model: a blank camera clock (0000:00:00) counts as no date.
    """
    m = re.match(r"((?:18|19|20)\d{2})[:\-]", ((exif or {}).get("datetime") or "").strip())
    return m.group(1) if m else ""


def year_choices(years) -> list[str]:
    """Browse filter values: 'All years', newest year first, then 'No date' if any is missing."""
    ys = list(years)
    out = [ALL_YEARS] + sorted({y for y in ys if y}, reverse=True)
    return out + [NO_DATE] if any(not y for y in ys) else out


def year_matches(year: str, choice: str) -> bool:
    if choice in ("", ALL_YEARS):
        return True
    return not year if choice == NO_DATE else year == choice


def catalog_rows(settings: Settings, cat: Catalog, key: str) -> list[dict]:
    """One dict per present file: path, EXIF, colours, dedup group and current result."""
    res = effective_results(cat, key, bool(settings.dedup.get("propagate")))
    exif = {r["sha256"]: dict(r) for r in cat.q("SELECT * FROM exif")}
    stats = {r["sha256"]: dict(r) for r in cat.q("SELECT * FROM color_stats")}
    groups = {r["sha256"]: r["group_id"] for r in cat.q("SELECT sha256, group_id FROM dedup")}
    cols: dict[str, list] = {}
    for r in cat.q("SELECT sha256, color_name, fraction, hex FROM colors ORDER BY sha256, rank"):
        cols.setdefault(r["sha256"], []).append((r["color_name"], r["fraction"], r["hex"]))
    failed = {r["sha256"]: r["error"] for r in cat.q(
        "SELECT sha256, error FROM vlm_results WHERE config_key=? AND status='failed' "
        "ORDER BY result_id", (key,))}
    out = []
    for f in cat.q("SELECT file_id, path, sha256, width, height FROM files WHERE missing=0 "
                   "ORDER BY path"):
        sha = f["sha256"]
        d = {"file_id": f["file_id"], "path": f["path"], "sha256": sha,
             "width": f["width"], "height": f["height"]}
        d["exif"] = exif.get(sha) or {}
        d["year"] = photo_year(d["exif"])
        d["stats"] = stats.get(sha) or {}
        d["colors"] = cols.get(sha, [])
        d["group_id"] = groups.get(sha)
        r = res.get(sha)
        d["result"] = json.loads(r[0]["json"]) if r else None
        d["result_id"] = r[0]["result_id"] if r else None
        d["propagated"] = bool(r and r[1])
        d["error"] = None if r else failed.get(sha)
        out.append(d)
    return out


def disagreements(row: dict) -> list[str]:
    """EXIF / pixel facts that contradict the model's labels."""
    r, e, s = row["result"], row["exif"], row["stats"]
    if not r:
        return []
    out = []
    g = r["primary_genre"]
    sh = e.get("shutter_s") or 0
    if e.get("is_long_exposure") and g in PEOPLE_GENRES:
        out.append(f"{sh:.0f} s exposure classified as {g}")
    if sh >= 10 and e.get("is_high_iso") and g != "astrophotography":
        out.append(f"{sh:.0f} s at ISO {e.get('iso')} (astro-like exposure) classified as {g}")
    if g == "astrophotography" and e.get("shutter_s") and sh < 1 / 30 and r["subgenre"] not in (
            "lunar", "solar", "solar_eclipse", "planetary", "lunar_eclipse"):
        out.append(f"astrophotography ({r['subgenre']}) at 1/{round(1 / sh)} s")
    dark = s.get("tonality") == "low_key" or g == "astrophotography"   # night skies have no chroma
    if s and s.get("is_monochrome") and not dark and "black_and_white" not in (
            r.get("techniques") or []):
        out.append("monochrome pixels but no black_and_white technique")
    if s and not s.get("is_monochrome") and "black_and_white" in (r.get("techniques") or []):
        out.append("black_and_white technique but the pixels have colour")
    if e.get("is_long_exposure") and r.get("time_of_day") == "day" and "long_exposure" not in (
            r.get("techniques") or []):
        out.append(f"{sh:.1f} s daytime exposure without long_exposure technique")
    return out


def row_tags(settings: Settings, row: dict) -> list[str]:
    """The hierarchical tags the sidecar gets for this photo, without the AI| root."""
    if not row["result"]:
        return []
    colors = [{"color_name": n, "fraction": f} for n, f, _h in row["colors"]]
    hier, _flat = build_tags(row["result"], colors, row["stats"] or None, settings.xmp,
                             float(settings.report["low_confidence"]))
    return [h.split("|", 1)[1] for h in hier]


def write_csv(rows: list[dict], path: str, settings: Settings | None = None) -> None:
    fields = ["path", "sha256", "primary_genre", "subgenre", "confidence", "secondary_genres",
              "people_present", "people_count", "people_role", "animal_type", "wild_or_domestic",
              "setting", "time_of_day", "weather_sky", "season", "techniques", "utility_flag",
              "main_subject", "caption", "keywords", "subject_tags", "colors", "tonality",
              "monochrome", "colourfulness", "mean_L", "camera", "lens", "focal_35mm", "aperture",
              "shutter_s", "iso", "datetime", "lat", "lon", "dup_group", "propagated", "error",
              "xmp_tags"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for row in rows:
            r, e, s = row["result"] or {}, row["exif"], row["stats"]
            p, a = r.get("people") or {}, r.get("animals") or {}
            w.writerow({
                "path": row["path"], "sha256": row["sha256"],
                "primary_genre": r.get("primary_genre"), "subgenre": r.get("subgenre"),
                "confidence": r.get("genre_confidence"),
                "secondary_genres": ";".join(r.get("secondary_genres") or []),
                "people_present": p.get("present"), "people_count": p.get("count_bin"),
                "people_role": p.get("role"), "animal_type": a.get("type"),
                "wild_or_domestic": a.get("wild_or_domestic"), "setting": r.get("setting"),
                "time_of_day": r.get("time_of_day"), "weather_sky": r.get("weather_sky"),
                "season": r.get("season"), "techniques": ";".join(r.get("techniques") or []),
                "utility_flag": r.get("utility_flag"), "main_subject": r.get("main_subject"),
                "caption": r.get("caption"), "keywords": ";".join(r.get("keywords") or []),
                "subject_tags": ";".join(r.get("subject_tags") or []),
                "colors": ";".join(f"{n}:{f:.2f}" for n, f, _ in row["colors"]),
                "tonality": s.get("tonality"), "monochrome": s.get("is_monochrome"),
                "colourfulness": round(s["colourfulness"], 1) if s.get("colourfulness") else None,
                "mean_L": round(s["mean_L"], 1) if s.get("mean_L") is not None else None,
                "camera": e.get("camera"), "lens": e.get("lens"), "focal_35mm": e.get("focal_35mm"),
                "aperture": e.get("aperture"), "shutter_s": e.get("shutter_s"), "iso": e.get("iso"),
                "datetime": e.get("datetime"), "lat": e.get("lat"), "lon": e.get("lon"),
                "dup_group": row["group_id"], "propagated": row["propagated"] or None,
                "error": row["error"],
                "xmp_tags": "; ".join(row_tags(settings, row)) if settings else None})


def thumb_data_url(settings: Settings, sha: str | None) -> str:
    if not sha:
        return ""
    p = settings.cache_path(sha, "thumb")
    try:
        with open(p, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode()
    except OSError:
        return ""


def throughput(cat: Catalog, key: str) -> dict:
    """Measured figures for the active configuration (all runs with this key)."""
    r = cat.one("SELECT SUM(n_ok), SUM(n_failed), SUM(wall_s) FROM runs WHERE config_key=? "
                "AND label NOT LIKE 'bench%' AND wall_s > 0", (key,))
    lat = [x[0] for x in cat.q("SELECT latency_s FROM vlm_results WHERE config_key=? AND "
                               "status='ok' ORDER BY latency_s", (key,))]
    tok = cat.one("SELECT AVG(tokens_in), AVG(tokens_out) FROM vlm_results WHERE config_key=? "
                  "AND status='ok'", (key,))
    n_ok, n_fail, wall = (r[0] or 0, r[1] or 0, r[2] or 0.0) if r else (0, 0, 0.0)
    pct = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else None  # noqa: E731
    return {"ok": n_ok, "failed": n_fail, "wall_s": wall,
            "per_min": n_ok / wall * 60 if wall else None,
            "p50": pct(0.5), "p95": pct(0.95),
            "tok_in": tok[0] if tok else None, "tok_out": tok[1] if tok else None}


CSS = """body{font:14px 'Segoe UI',sans-serif;background:#0b1113;color:#e4f1f0;margin:24px}
h1{font-weight:600}h2{margin-top:32px;border-bottom:1px solid #21363b;padding-bottom:6px}
table{border-collapse:collapse}td,th{padding:4px 10px;text-align:left;border-bottom:1px solid #132024}
.bar{background:linear-gradient(90deg,#1d7a73,#56c2b6);height:10px;border-radius:5px}
.grid{display:flex;flex-wrap:wrap;gap:10px}.card{width:220px;background:#132024;border-radius:8px;
padding:8px;font-size:12px}.card img{width:100%;border-radius:6px}.muted{color:#8fb0ad}
.warn{color:#e2b36a}.kpi{display:inline-block;margin-right:28px}.kpi b{font-size:22px;display:block;
color:#5cc8bc}"""


def _card(settings: Settings, row: dict, note: str = "") -> str:
    r = row["result"] or {}
    img = thumb_data_url(settings, row["sha256"])
    return (f'<div class="card"><img src="{img}" loading="lazy">'
            f'<div><b>{html.escape(str(r.get("primary_genre")))}/{html.escape(str(r.get("subgenre")))}'
            f'</b> {float(r.get("genre_confidence") or 0):.2f}</div>'
            f'<div>{html.escape(r.get("caption") or row.get("error") or "")}</div>'
            + (f'<div class="warn">{html.escape(note)}</div>' if note else "")
            + f'<div class="muted">{html.escape(row["path"])}</div></div>')


def build_report(settings: Settings, cat: Catalog, key: str, ident: dict,
                 out_dir: str | None = None) -> dict:
    """Write catalog CSV + HTML report; log the summary. Returns paths and counts."""
    out_dir = out_dir or settings.report_dir
    os.makedirs(out_dir, exist_ok=True)
    rows = catalog_rows(settings, cat, key)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    csv_path = os.path.join(out_dir, f"catalog_{ts}.csv")
    html_path = os.path.join(out_dir, f"report_{ts}.html")
    write_csv(rows, csv_path, settings)
    tags_path = write_tags_listing(settings, rows, os.path.join(out_dir, f"tags_{ts}.html"), key)

    tagged = [r for r in rows if r["result"]]
    low_t = float(settings.report["low_confidence"])
    low = [r for r in tagged if float(r["result"]["genre_confidence"]) < low_t]
    failed = [r for r in rows if not r["result"] and r["error"]]
    genres = Counter(r["result"]["primary_genre"] for r in tagged)
    subs = Counter((r["result"]["primary_genre"], r["result"]["subgenre"]) for r in tagged)
    dis = [(r, d) for r in tagged for d in disagreements(r)]
    tp = throughput(cat, key)

    log.info("=== report ===")
    log.info("photos %d, tagged %d, failed %d, low confidence (<%.2f) %d, disagreements %d",
             len(rows), len(tagged), len(failed), low_t, len(low), len(dis))
    for g, n in genres.most_common():
        log.info("  %-24s %5d", g, n)
    if tp["per_min"]:
        log.info("throughput %.1f photos/min, latency p50 %.1fs p95 %.1fs, tokens in/out %.0f/%.0f",
                 tp["per_min"], tp["p50"], tp["p95"], tp["tok_in"], tp["tok_out"])

    rng = random.Random(settings.report.get("seed", 42))
    k = int(settings.report["sample_per_genre"])
    top = max(genres.values()) if genres else 1
    parts = [f"<!doctype html><meta charset='utf-8'><title>PhotoMancer report</title>"
             f"<style>{CSS}</style><h1>PhotoMancer report</h1>"
             f"<p class='muted'>{html.escape(ts)} · config {html.escape(key)} · "
             f"{html.escape(json.dumps(ident))}</p>"]
    kpis = [("Photos", len(rows)), ("Tagged", len(tagged)), ("Failed", len(failed)),
            (f"Confidence < {low_t}", len(low)), ("EXIF/VLM disagreements", len(dis))]
    if tp["per_min"]:
        kpis += [("Photos / min", f"{tp['per_min']:.1f}"), ("Latency p50", f"{tp['p50']:.1f} s"),
                 ("Latency p95", f"{tp['p95']:.1f} s")]
    parts.append("<div>" + "".join(f"<span class='kpi'><b>{v}</b>{html.escape(n)}</span>"
                                   for n, v in kpis) + "</div>")
    parts.append("<h2>Genres</h2><table>")
    for g, n in genres.most_common():
        sub = ", ".join(f"{s} {c}" for (gg, s), c in subs.most_common() if gg == g)
        parts.append(f"<tr><td>{html.escape(g)}</td><td>{n}</td><td style='width:220px'>"
                     f"<div class='bar' style='width:{n / top * 100:.0f}%'></div></td>"
                     f"<td class='muted'>{html.escape(sub)}</td></tr>")
    parts.append("</table>")
    if dis:
        parts.append(f"<h2>EXIF / pixel vs model disagreements ({len(dis)}; first 60)</h2>"
                     "<div class='grid'>" + "".join(_card(settings, r, d) for r, d in dis[:60])
                     + "</div>")
    if low:
        smp = sorted(low, key=lambda r: r["result"]["genre_confidence"])[:40]
        parts.append(f"<h2>Lowest confidence ({len(low)}; 40 lowest)</h2><div class='grid'>"
                     + "".join(_card(settings, r) for r in smp) + "</div>")
    if failed:
        parts.append(f"<h2>Failed ({len(failed)}; first 40)</h2><div class='grid'>"
                     + "".join(_card(settings, r) for r in failed[:40]) + "</div>")
    parts.append(f"<h2>Contact sheet - random {k} per genre</h2>")
    for g, _n in genres.most_common():
        members = [r for r in tagged if r["result"]["primary_genre"] == g]
        pick = rng.sample(members, min(k, len(members)))
        parts.append(f"<h3>{html.escape(g)} ({len(members)})</h3><div class='grid'>"
                     + "".join(_card(settings, r) for r in pick) + "</div>")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    log.info("Report written: %s", html_path)
    log.info("Catalog CSV: %s", csv_path)
    log.info("Per-photo tag listing: %s", tags_path)
    return {"html": html_path, "csv": csv_path, "tags": tags_path, "tagged": len(tagged),
            "failed": len(failed)}


def write_tags_listing(settings: Settings, rows: list[dict], path: str, key: str) -> str:
    """One row per photo: thumbnail, path, genre, confidence, caption and its sidecar tags.

    Up to 500 photos the thumbnails are embedded (one portable file); above that they
    are linked from the local cache (file:// URLs) so the page stays small - that
    version is meant to be opened on this PC.
    """
    body = []
    embed = len(rows) <= 500               # small lists travel as one self-contained file
    for r in rows:
        res = r["result"]
        th = settings.cache_path(r["sha256"], "thumb") if r["sha256"] else ""
        if th and os.path.exists(th):
            src = (thumb_data_url(settings, r["sha256"]) if embed
                   else "file:///" + th.replace(os.sep, "/"))
            img = f'<img src="{html.escape(src)}" loading="lazy">'
        else:
            img = ""
        if res:
            head = (f'<b>{html.escape(res["primary_genre"])}</b> / {html.escape(res["subgenre"])}'
                    f' <span class="muted">{float(res["genre_confidence"]):.2f}</span>')
            tags = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in row_tags(settings, r))
            cap = html.escape(res.get("caption", ""))
        else:
            head, tags = '<span class="warn">not tagged</span>', ""
            cap = html.escape(r["error"] or "waiting for the next run")
        body.append(f"<tr><td class='th'>{img}</td><td><div class='path'>{html.escape(r['path'])}"
                    f"</div><div>{head}</div><div>{cap}</div><div class='tags'>{tags}</div></td></tr>")
    css = CSS + """.th img{width:160px;border-radius:6px}.th{width:170px;vertical-align:top}
td{vertical-align:top;padding:10px}.path{font-family:Consolas,monospace;color:#9fe3da;margin-bottom:4px}
.tag{display:inline-block;background:#19292e;border:1px solid #21363b;border-radius:10px;
padding:1px 8px;margin:3px 4px 0 0;font-size:12px;color:#c9e4e1}"""
    n_t = sum(1 for r in rows if r["result"])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"<!doctype html><meta charset='utf-8'><title>Photo tags</title><style>{css}"
                 f"</style><h1>Photo tags</h1><p class='muted'>{n_t} of {len(rows)} photos tagged "
                 f"· config {html.escape(key)} · tags as written to the XMP sidecars "
                 f"(under the AI root)</p><table>{''.join(body)}</table>")
    return path

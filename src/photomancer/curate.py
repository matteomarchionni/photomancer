"""Curate: the vision model picks the best photos for a theme - the photo
counterpart of BiblioMancer's deep research.

    python -m photomancer curate "misty mountains at dawn" --top 12 --pool 48

1. search by meaning finds the `pool` best-matching photos (semantic index);
2. the model looks at each of them and rates how well it fits the theme and
   how strong it is as a photograph (0-10 each, plus one sentence why);
3. the `top` best (0.6 x fit + 0.4 x quality) go into
   <data>/reports/curate_<date>_<theme>/ as a contact sheet (index.html,
   opened when done) and selection.txt with the full paths - ready to drag
   into Immich, an album or a print order.

write_selection() is also what the panel's "Save list" (Browse) uses.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import numpy as np
import requests

from .classify import active_config
from .config import Settings
from .db import Catalog
from .report import catalog_rows, thumb_data_url
from .taxonomy import load_taxonomy
from .vlm_client import encode_image, strip_to_json

log = logging.getLogger("photomancer.curate")

SYSTEM = ("You are a photo editor choosing images for a themed selection. You see ONE photo and "
          "the theme. Rate honestly: 'fit' = how well the photo matches the theme (0 = not at "
          "all, 10 = perfect); 'quality' = how strong it is as a photograph - composition, light, "
          "sharpness where it matters, moment (0-10). 'reason' is one short sentence.")
SCHEMA = {"type": "object", "properties": {
    "fit": {"type": "integer", "minimum": 0, "maximum": 10},
    "quality": {"type": "integer", "minimum": 0, "maximum": 10},
    "reason": {"type": "string"}}, "required": ["fit", "quality", "reason"],
    "additionalProperties": False}


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()[:40] or "selection"


def write_selection(settings: Settings, rows: list[dict], title: str, notes: dict | None = None,
                    folder: str | None = None) -> str:
    """Contact sheet (index.html) + selection.txt for `rows` (catalog rows). Returns the HTML path."""
    folder = folder or os.path.join(settings.report_dir,
                                    f"selection_{datetime.now():%Y-%m-%d_%H%M}_{_slug(title)}")
    os.makedirs(folder, exist_ok=True)
    notes = notes or {}
    with open(os.path.join(folder, "selection.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{title}\n{len(rows)} photo(s), {datetime.now():%Y-%m-%d %H:%M}\n\n")
        for r in rows:
            fh.write(os.path.join(settings.photo_root, r["path"]) + "\n")
    cards = []
    for i, r in enumerate(rows, 1):
        res = r["result"] or {}
        cards.append(
            f'<div class="card"><img src="{thumb_data_url(settings, r["sha256"])}">'
            f'<div><b>{i}.</b> {html.escape(res.get("caption") or "")}</div>'
            + (f'<div class="note">{html.escape(notes[r["sha256"]])}</div>' if r["sha256"] in notes else "")
            + f'<div class="muted">{html.escape(r["path"])}</div></div>')
    page = ("<!doctype html><meta charset='utf-8'><title>" + html.escape(title) + "</title><style>"
            "body{font:14px 'Segoe UI',sans-serif;background:#0b1113;color:#e4f1f0;margin:24px}"
            ".grid{display:flex;flex-wrap:wrap;gap:12px}.card{width:260px;background:#132024;"
            "border-radius:8px;padding:8px;font-size:12px}.card img{width:100%;border-radius:6px}"
            ".muted{color:#8fb0ad;word-break:break-all}.note{color:#9fe3da;margin:4px 0}</style>"
            f"<h1>{html.escape(title)}</h1><p class='muted'>{len(rows)} photo(s) - full paths in "
            "selection.txt next to this page.</p><div class='grid'>" + "".join(cards) + "</div>")
    path = os.path.join(folder, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return path


def _rate(settings: Settings, theme: str, image: str) -> tuple[dict | None, str, float]:
    t0 = time.time()
    try:
        url = encode_image(image, int(settings.vlm["image_side"]), int(settings.vlm.get("jpeg_quality", 90)))
        payload = {"model": settings.lm["model"], "stream": False, "temperature": 0.1, "max_tokens": 200,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": [
                                    {"type": "text", "text": f"Theme: {theme}"},
                                    {"type": "image_url", "image_url": {"url": url}}]}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "rating", "strict": True, "schema": SCHEMA}}}
        if settings.lm.get("reasoning_effort"):
            payload["reasoning_effort"] = settings.lm["reasoning_effort"]
        r = requests.post(f"{settings.lm['base_url']}/chat/completions", json=payload,
                          timeout=float(settings.vlm["timeout_s"]))
        r.raise_for_status()
        data = json.loads(strip_to_json(r.json()["choices"][0]["message"].get("content") or ""))
        data["fit"], data["quality"] = int(data["fit"]), int(data["quality"])
        return data, "", time.time() - t0
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}", time.time() - t0


def curate(settings: Settings, theme: str, top: int = 12, pool: int = 48, lm_setup: bool = True) -> int:
    from .pipeline import ensure_server, say
    from .search import SearchIndex, wake
    t0 = time.time()
    tax = load_taxonomy(settings.taxonomy_path)
    cfg = active_config(settings, tax)
    cat = Catalog(settings.db_path)
    try:
        say("find candidates")
        rows = {r["sha256"]: r for r in catalog_rows(settings, cat, cfg["key"]) if r["result"]}
        idx = SearchIndex(settings, cat, cfg["key"])
        if not len(idx):
            log.info("ERROR: the search index is empty - run Tag new photos / index first")
            return 1
        ok, why = wake(settings)
        if not ok:
            log.info("ERROR: search by meaning needs LM Studio: %s", why)
            return 1
        sc = idx.scores(theme)
        order = [i for i in np.argsort(-sc) if idx.shas[i] in rows][:pool]
        cand = [(idx.shas[i], float(sc[i])) for i in order]
        log.info("%d candidate(s) for %r (best match %.2f)", len(cand), theme, cand[0][1] if cand else 0)
        ensure_server(settings, lm_setup)
        say("rate photos")
        rated, n = [], len(cand)
        with ThreadPoolExecutor(max_workers=max(1, int(settings.vlm["concurrency"]))) as ex:
            futs = {ex.submit(_rate, settings, theme, settings.cache_path(sha)): (sha, s)
                    for sha, s in cand}
            for k, fut in enumerate(as_completed(futs), 1):
                sha, s = futs[fut]
                data, err, secs = fut.result()
                path = rows[sha]["path"]
                if data:
                    rated.append((0.6 * data["fit"] + 0.4 * data["quality"] + 0.5 * s, sha, data))
                    log.info("[%d/%d] OK %.1fs fit %d quality %d | %s", k, n, secs, data["fit"],
                             data["quality"], path)
                else:
                    log.info("[%d/%d] FAIL %.1fs %s | %s", k, n, secs, err[:80], path)
        say("write selection")
        rated.sort(key=lambda t: -t[0])
        best = rated[:top]
        notes = {sha: f"fit {d['fit']}/10 · quality {d['quality']}/10 - {d['reason']}"
                 for _s, sha, d in best}
        title = f"Curated: {theme}"
        folder = os.path.join(settings.report_dir, f"curate_{datetime.now():%Y-%m-%d_%H%M}_{_slug(theme)}")
        path = write_selection(settings, [rows[sha] for _s, sha, _d in best], title, notes, folder)
        log.info("Report written: %s", path)
        return 0 if best else 1
    finally:
        cat.close()
        say(f"pipeline finished in {(time.time() - t0) / 60:.1f} min")

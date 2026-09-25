"""Export the catalog for other AI agents (Claude, ChatGPT, a local model),
like BiblioMancer's export.

    python -m photomancer export            (also runs at the end of every run)

Writes into <backup_dir>/export (synced) or, without a backup folder,
<data_dir>/export:

    photo_catalog_index.md   self-describing header + one line per photo:
                             path | genre/subgenre | caption | keywords | date
    photo_catalog.jsonl      one JSON record per photo, every field
    photo_catalog.sqlite     table `photos` + `photos_fts` (FTS5 full-text)
    README_FOR_AGENTS.md     what each file is and how to ask about it

An agent given photo_catalog_index.md can answer "find my foggy mountain
shots from 2019" and return the file paths.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime

from .config import Settings
from .db import Catalog
from .report import catalog_rows, row_tags

log = logging.getLogger("photomancer.export")

HOW_TO = """\
## How to use this file (for AI agents)

This is the catalog of a personal photo archive. Every photo was looked at by
a local vision-language model and described with the same fields: a primary
genre and subgenre (from a fixed taxonomy), a one-sentence caption, keywords,
subject tags, people/animals, setting, time of day, weather, season,
techniques and dominant colours. Camera data and the capture date come from
the photo's EXIF.

- Each line of the index below is one photo; `path` is relative to the
  archive root given in the header. Answer with those paths.
- Genres and tags are model output: good for finding photos, not ground truth.
- For richer queries use photo_catalog.sqlite (table `photos`, full-text
  index `photos_fts`) or photo_catalog.jsonl.
"""


def _record(settings: Settings, row: dict) -> dict:
    r, e = row["result"] or {}, row["exif"]
    p, a = r.get("people") or {}, r.get("animals") or {}
    return {
        "path": row["path"], "sha256": row["sha256"],
        "genre": r.get("primary_genre"), "subgenre": r.get("subgenre"),
        "confidence": r.get("genre_confidence"), "secondary_genres": r.get("secondary_genres") or [],
        "caption": r.get("caption"), "main_subject": r.get("main_subject"),
        "keywords": r.get("keywords") or [], "subject_tags": r.get("subject_tags") or [],
        "people": p.get("present"), "people_count": p.get("count_bin"),
        "animal": a.get("type") if a.get("present") else None,
        "setting": r.get("setting"), "time_of_day": r.get("time_of_day"),
        "weather_sky": r.get("weather_sky"), "season": r.get("season"),
        "techniques": r.get("techniques") or [],
        "colors": [n for n, f, _h in row["colors"] if f >= float(settings.xmp["min_color_fraction"])],
        "date": (e.get("datetime") or "")[:10], "camera": e.get("camera"), "lens": e.get("lens"),
        "lat": e.get("lat"), "lon": e.get("lon"),
        "tags": row_tags(settings, row),
    }


def export_dir(settings: Settings) -> str:
    return os.path.join(settings.backup_dir, "export") if settings.backup_dir else settings.export_dir


def export(settings: Settings, cat: Catalog, key: str) -> str:
    """Write the agent export; returns its folder. Never fails the run."""
    dest = export_dir(settings)
    log.info("=== export for agents to %s ===", dest)
    try:
        os.makedirs(dest, exist_ok=True)
        recs = [_record(settings, r) for r in catalog_rows(settings, cat, key) if r["result"]]
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        with open(os.path.join(dest, "photo_catalog.jsonl"), "w", encoding="utf-8") as fh:
            for x in recs:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
        cell = lambda s: " ".join(str(s or "").split()).replace("|", "/")  # noqa: E731
        lines = [f"# Photo catalog", "", f"_{len(recs)} photos - exported {stamp} - archive root: "
                 f"`{settings.photo_root}`_", "", HOW_TO, "## Index", "",
                 "| path | genre / subgenre | caption | keywords | date |", "|---|---|---|---|---|"]
        lines += [f"| {cell(x['path'])} | {x['genre']} / {x['subgenre']} | {cell(x['caption'])} "
                  f"| {cell(', '.join(x['keywords'][:8]))} | {x['date']} |" for x in recs]
        with open(os.path.join(dest, "photo_catalog_index.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        db = os.path.join(dest, "photo_catalog.sqlite")
        tmp = db + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        c = sqlite3.connect(tmp)
        cols = ["path", "sha256", "genre", "subgenre", "confidence", "caption", "main_subject",
                "keywords", "subject_tags", "people", "animal", "setting", "time_of_day",
                "weather_sky", "season", "techniques", "colors", "date", "camera", "lens", "lat",
                "lon", "tags"]
        c.execute(f"CREATE TABLE photos ({', '.join(cols)})")
        c.execute("CREATE VIRTUAL TABLE photos_fts USING fts5(path, caption, main_subject, keywords,"
                  " subject_tags, tags, content='photos', content_rowid='rowid')")
        flat = lambda v: "; ".join(v) if isinstance(v, list) else v  # noqa: E731
        c.executemany(f"INSERT INTO photos VALUES ({', '.join('?' * len(cols))})",
                      [tuple(flat(x.get(k)) for k in cols) for x in recs])
        c.execute("INSERT INTO photos_fts(rowid, path, caption, main_subject, keywords, subject_tags,"
                  " tags) SELECT rowid, path, caption, main_subject, keywords, subject_tags, tags "
                  "FROM photos")
        c.commit()
        c.close()
        os.replace(tmp, db)
        with open(os.path.join(dest, "README_FOR_AGENTS.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# Photo catalog export ({stamp})\n\n{HOW_TO}\n## Files\n\n"
                     "- `photo_catalog_index.md` - give this one file to an agent.\n"
                     "- `photo_catalog.jsonl` - one JSON record per photo.\n"
                     "- `photo_catalog.sqlite` - SQL + full-text: "
                     "`SELECT path FROM photos WHERE rowid IN (SELECT rowid FROM photos_fts "
                     "WHERE photos_fts MATCH 'fog AND mountain')`\n\n"
                     "Example prompt: *Use photo_catalog_index.md as my photo archive. List the "
                     "paths of my best foggy mountain landscapes from 2019.*\n")
        log.info("export written: %d photos", len(recs))
    except Exception as exc:
        log.info("WARNING export not written (%s: %s)", type(exc).__name__, exc)
    return dest

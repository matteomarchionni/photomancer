"""Stage 1 - scan: list every JPEG under photo_root and record path/size/mtime.

Cheap (no file is opened). A file whose size or mtime changed loses its
content hash, which sends it back through `prepare`. Files that disappeared
are flagged `missing` (their results stay, for when they come back).
"""

from __future__ import annotations

import logging
import os

from .config import Settings
from .db import Catalog, now_iso

log = logging.getLogger("photomancer.scan")
JPEG_EXT = (".jpg", ".jpeg")


def long_path(p: str) -> str:
    r"""Windows long-path form (\\?\) for paths over 240 characters."""
    if os.name == "nt" and len(p) > 240 and not p.startswith("\\\\?\\"):
        return "\\\\?\\" + os.path.abspath(p)
    return p


def iter_jpegs(root: str, exclude: set[str]):
    """Yield (relative path, size, mtime) for every JPEG, depth-first, sorted."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(long_path(d)) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError as exc:
            log.warning("WARNING cannot list %s: %s", d, exc)
            continue
        subdirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name.lower() not in exclude and not e.name.startswith("."):
                        subdirs.append(os.path.join(d, e.name))
                elif e.name.lower().endswith(JPEG_EXT) and not e.name.startswith("._"):
                    st = e.stat()
                    full = os.path.join(d, e.name)
                    yield os.path.relpath(full, root), st.st_size, st.st_mtime
            except OSError as exc:
                log.warning("WARNING cannot stat %s: %s", e.path, exc)
        stack.extend(reversed(subdirs))


def scan(settings: Settings, cat: Catalog) -> dict:
    """Update `files` from disk. Returns counts {total, new, changed, missing}."""
    known = {r["path"]: (r["file_id"], r["size"], r["mtime"], r["missing"])
             for r in cat.q("SELECT file_id, path, size, mtime, missing FROM files")}
    seen: set[str] = set()
    new_rows, changed, back = [], [], []
    ts = now_iso()
    for rel, size, mtime in iter_jpegs(settings.photo_root, settings.exclude_dirs):
        seen.add(rel)
        k = known.get(rel)
        if k is None:
            new_rows.append((rel, size, mtime, ts))
        elif k[1] != size or abs((k[2] or 0) - mtime) > 1.0:
            changed.append((size, mtime, ts, k[0]))
        elif k[3]:
            back.append((k[0],))
    gone = [(v[0],) for p, v in known.items() if p not in seen and not v[3]]
    cat.x("INSERT INTO files(path, size, mtime, scanned_at) VALUES(?,?,?,?)", new_rows, many=True)
    # content changed: forget the hash so `prepare` reads it again
    cat.x("UPDATE files SET size=?, mtime=?, scanned_at=?, sha256=NULL, missing=0 WHERE file_id=?",
          changed, many=True)
    cat.x("DELETE FROM stage_status WHERE file_id=? AND stage='prepare'",
          [(c[3],) for c in changed], many=True)
    cat.x("UPDATE files SET missing=0 WHERE file_id=?", back, many=True)
    cat.x("UPDATE files SET missing=1 WHERE file_id=?", gone, many=True)
    out = {"total": len(seen), "new": len(new_rows), "changed": len(changed),
           "missing": len(gone), "returned": len(back)}
    log.info("%d JPEG(s) found: %d new, %d changed, %d missing, %d back",
             out["total"], out["new"], out["changed"], out["missing"], out["returned"])
    return out

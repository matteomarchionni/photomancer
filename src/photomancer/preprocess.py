"""Stage 2 - prepare (CPU, parallel): one read of each original does it all.

Reading 75 GB from the Google Drive mount is the slow part, so every
per-file job happens on the bytes already in memory:

    sha256 -> EXIF -> orientation + sRGB -> 1024 px cache JPEG (for the VLM)
           -> 256 px thumbnail -> colours (k-means Lab) + pHash

Cache files are keyed by the content hash. Workers never touch the
database; the parent process writes their results.
"""

from __future__ import annotations

import hashlib
import io
import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, Iterable

from PIL import Image, ImageCms, ImageOps

from .colors import analyse_colors
from .config import Settings
from .db import Catalog, now_iso
from .dedup import phash
from .exif import extract_exif
from .scan import long_path

log = logging.getLogger("photomancer.prepare")
Image.MAX_IMAGE_PIXELS = 400_000_000          # big panoramas are legitimate here
_SRGB = None


def _to_srgb(im: Image.Image) -> Image.Image:
    """Convert from an embedded ICC profile to sRGB (Adobe RGB exports etc.)."""
    global _SRGB
    icc = im.info.get("icc_profile")
    if icc and im.mode in ("RGB", "CMYK"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in (ImageCms.getProfileDescription(src) or "").lower():
                _SRGB = _SRGB or ImageCms.createProfile("sRGB")
                return ImageCms.profileToProfile(im, src, _SRGB, outputMode="RGB")
        except Exception:
            pass                                   # broken profile: treat as sRGB
    return im.convert("RGB")


def _save_atomic(img: Image.Image, path: str, quality: int) -> None:
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path + ".tmp", "JPEG", quality=quality, optimize=True)
    os.replace(path + ".tmp", path)


def prepare_one(job: dict) -> dict:
    """Worker: analyse one file. Never raises - errors come back in 'error'."""
    out = {"file_id": job["file_id"], "path": job["path"], "error": None}
    t0 = time.time()
    try:
        with open(long_path(job["full"]), "rb") as fh:
            blob = fh.read()
        if not blob:
            raise ValueError("empty file (0 bytes) - nothing to tag; restore it from a backup")
        sha = hashlib.sha256(blob).hexdigest()
        out["sha256"] = sha
        im = Image.open(io.BytesIO(blob))
        out["width"], out["height"] = im.size
        out["exif"] = extract_exif(im, job["exif_cfg"])
        side = int(job["cache_side"])
        im.draft("RGB", (side, side))              # fast DCT-domain downscale for JPEG
        im = _to_srgb(ImageOps.exif_transpose(im))
        im.thumbnail((side, side), Image.Resampling.LANCZOS)
        _save_atomic(im, os.path.join(job["img_root"], sha[:2], sha + ".jpg"), job["quality"])
        th = im.copy()
        th.thumbnail((job["thumb_side"], job["thumb_side"]), Image.Resampling.LANCZOS)
        _save_atomic(th, os.path.join(job["thumb_root"], sha[:2], sha + ".jpg"),
                     job["thumb_quality"])
        out["clusters"], out["stats"] = analyse_colors(th, job["palette"], job["colors_cfg"])
        out["phash"] = phash(th)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["seconds"] = time.time() - t0
    return out


def make_job(settings: Settings, palette: dict, row) -> dict:
    """Everything a worker needs, as plain picklable data."""
    return {"file_id": row["file_id"], "path": row["path"],
            "full": os.path.join(settings.photo_root, row["path"]),
            "exif_cfg": settings.exif, "colors_cfg": settings.colors, "palette": palette,
            "cache_side": settings.pre["cache_side"], "thumb_side": settings.pre["thumb_side"],
            "quality": int(settings.vlm["jpeg_quality"]),
            "thumb_quality": int(settings.pre["thumb_quality"]),
            "img_root": settings.img_cache, "thumb_root": settings.thumb_cache}


def store_result(cat: Catalog, r: dict) -> None:
    """Write one worker result into the catalog (parent process only)."""
    fid = r["file_id"]
    if r["error"]:
        cat.set_stage(fid, "prepare", "failed", r["error"])
        return
    sha, e, st = r["sha256"], r["exif"], r["stats"]
    with cat.lock:
        c = cat.conn
        c.execute("UPDATE files SET sha256=?, width=?, height=? WHERE file_id=?",
                  (sha, r["width"], r["height"], fid))
        c.execute("INSERT OR REPLACE INTO exif VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (sha, e["camera"], e["lens"], e["focal_mm"], e["focal_35mm"], e["aperture"],
                   e["shutter_s"], e["iso"], e["datetime"], e["lat"], e["lon"],
                   e["is_long_exposure"], e["is_high_iso"], e["is_telephoto"], e["has_gps"]))
        c.execute("DELETE FROM colors WHERE sha256=?", (sha,))
        c.executemany("INSERT INTO colors VALUES(?,?,?,?,?,?,?,?)",
                      [(sha, i, k["lab_l"], k["lab_a"], k["lab_b"], k["hex"], k["fraction"],
                        k["color_name"]) for i, k in enumerate(r["clusters"])])
        c.execute("INSERT OR REPLACE INTO color_stats VALUES(?,?,?,?,?,?)",
                  (sha, st["mean_L"], st["std_L"], st["colourfulness"], st["is_monochrome"],
                   st["tonality"]))
        c.execute("INSERT INTO dedup(sha256, phash) VALUES(?,?) ON CONFLICT(sha256) "
                  "DO UPDATE SET phash=excluded.phash", (sha, r["phash"]))
        c.execute("INSERT INTO stage_status(file_id, stage, status, error, attempts, updated_at) "
                  "VALUES(?, 'prepare', 'ok', NULL, 1, ?) ON CONFLICT(file_id, stage) DO UPDATE "
                  "SET status='ok', error=NULL, attempts=attempts+1, updated_at=excluded.updated_at",
                  (fid, now_iso()))
        c.commit()


def pending_prepare(cat: Catalog, retry_failed: bool = False, max_attempts: int = 3) -> list:
    """Files still to prepare: no hash yet, or never prepared successfully."""
    return cat.q(
        "SELECT f.file_id, f.path FROM files f LEFT JOIN stage_status s "
        "ON s.file_id = f.file_id AND s.stage = 'prepare' "
        "WHERE f.missing = 0 AND (f.sha256 IS NULL OR s.status IS NULL OR s.status != 'ok') "
        "AND (s.status IS NULL OR ? OR s.attempts < ?) ORDER BY f.path",
        (1 if retry_failed else 0, max_attempts))


def run_prepare(settings: Settings, cat: Catalog, rows: Iterable, palette: dict,
                on_done: Callable[[dict], None] | None = None,
                should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Prepare `rows` in a process pool; call on_done(result) as each one finishes."""
    rows = list(rows)
    n_ok = n_fail = 0
    if not rows:
        return {"ok": 0, "failed": 0}
    workers = max(1, min(int(settings.pre["workers"]), len(rows)))
    t0 = time.time()
    # spawn, as on Windows and macOS: Linux's default fork copies the threads of
    # an already-running process (the panel, numpy/BLAS pools) and a worker can
    # deadlock on a lock one of them held - preparation then never finishes.
    ex = ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"))
    try:
        futs = [ex.submit(prepare_one, make_job(settings, palette, r)) for r in rows]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            store_result(cat, r)
            if r["error"]:
                n_fail += 1
                log.info("FAIL prepare %s: %s", r["path"], r["error"])
            else:
                n_ok += 1
            if i % 25 == 0 or i == len(futs):
                log.info("prepared %d/%d (%.1f photos/s)", i, len(futs),
                         i / max(1e-6, time.time() - t0))
            if on_done:
                on_done(r)
            if should_stop():
                log.info("time limit reached - stopping preparation")
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return {"ok": n_ok, "failed": n_fail}

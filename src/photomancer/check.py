"""Gate G1 - environment check: OS, Python, packages, LM Studio, model, and a
real classification of 3 photos (vision works, reasoning off, structured
output honoured). Prints what it measures; decides nothing.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import platform
import random
import sys
import tempfile

from . import lmstudio
from .classify import active_config
from .config import Settings
from .exif import exif_summary
from .preprocess import prepare_one
from .prompt import user_text
from .scan import iter_jpegs
from .taxonomy import load_taxonomy
from .vlm_client import VlmClient

log = logging.getLogger("photomancer.check")


def run_check(settings: Settings, n_images: int = 3, seed: int = 0) -> int:
    log.info("=== environment ===")
    log.info("OS       : %s %s (%s)", platform.system(), platform.version(), platform.machine())
    log.info("Python   : %s  (%s)", sys.version.split()[0], sys.executable)
    for mod in ("PIL", "numpy", "scipy", "sklearn", "skimage", "pydantic", "yaml", "requests"):
        try:
            m = importlib.import_module(mod)
            log.info("  %-9s %s", mod, getattr(m, "__version__", "?"))
        except ImportError:
            log.info("  %-9s MISSING", mod)
    gpu = lmstudio.gpu_memory_mb()
    log.info("GPU VRAM in use: %s MB", gpu if gpu is not None else "unknown (nvidia-smi missing)")
    tax = load_taxonomy(settings.taxonomy_path)
    log.info("Taxonomy : %s v%s (%s): %d genres, %d subgenres", settings.taxonomy_path,
             tax.version, tax.sha8, len(tax.genres), len(tax.all_subgenres))

    log.info("=== LM Studio ===")
    lms = settings.lm["lms_path"]
    ok, msg = lmstudio.check_model(lms, settings.lm["model"], int(settings.lm["context_per_request"]))
    log.info("%s %s", "OK" if ok else "ERROR", msg)
    if not ok:
        return 2
    lmstudio.setup(settings.lm, int(settings.vlm["concurrency"]))
    up, ids = lmstudio.server_up(settings.lm["base_url"])
    log.info("server %s at %s; offers %s", "up" if up else "DOWN", settings.lm["base_url"], ids)
    if not up:
        return 4

    log.info("=== test photos ===")
    files = [p for p, _s, _m in iter_jpegs(settings.photo_root, settings.exclude_dirs)]
    if not files:
        log.info("ERROR no JPEG under %s", settings.photo_root)
        return 5
    rnd = random.Random(seed or None)
    pick = rnd.sample(files, min(n_images, len(files)))
    cfg = active_config(settings, tax)
    client = VlmClient(settings.lm, settings.vlm, tax, cfg["system"])
    tmp = tempfile.mkdtemp(prefix="photomancer_check_")
    rc = 0
    for rel in pick:
        job = {"file_id": 0, "path": rel, "full": os.path.join(settings.photo_root, rel),
               "exif_cfg": settings.exif, "colors_cfg": settings.colors, "palette": tax.palette,
               "cache_side": settings.pre["cache_side"], "thumb_side": settings.pre["thumb_side"],
               "quality": int(settings.vlm["jpeg_quality"]), "thumb_quality": 85,
               "img_root": tmp, "thumb_root": tmp}
        r = prepare_one(job)
        if r["error"]:
            log.info("FAIL prepare %s: %s", rel, r["error"])
            rc = 1
            continue
        exif_line = exif_summary(r["exif"]) if settings.vlm["exif_context"] else ""
        ans = client.classify(os.path.join(tmp, r["sha256"][:2], r["sha256"] + ".jpg"),
                              user_text(exif_line))
        colors = ", ".join(f"{c['color_name']} {c['fraction']:.0%}" for c in r["clusters"][:3])
        log.info("%s %s", "OK" if ans.ok else "FAIL", rel)
        log.info("  latency %.1fs, tokens in %d / out %d, reasoning tokens %d",
                 ans.latency_s, ans.tokens_in, ans.tokens_out, ans.reasoning_tokens)
        log.info("  EXIF: %s", exif_line or "(none)")
        log.info("  colours: %s; tonality %s; monochrome %s", colors, r["stats"]["tonality"],
                 bool(r["stats"]["is_monochrome"]))
        if ans.ok:
            log.info("  %s", json.dumps(ans.data, ensure_ascii=False))
            if ans.warnings:
                log.info("  WARNING %s", "; ".join(ans.warnings))
        else:
            log.info("  ERROR %s", ans.error)
            rc = 1
        if ans.reasoning_tokens:
            log.info("  WARNING reasoning is ON (%d tokens) - check lmstudio.reasoning_effort",
                     ans.reasoning_tokens)
    return rc

"""The whole run in one place, so the panel, the scheduled task and the
command line all execute the same sequence:

    1. scan the photo folders
    2. start LM Studio and load the model (only when there is work)
    3. prepare (CPU pool) and tag (GPU requests) - overlapped: a photo is
       sent to the model as soon as its preparation finishes
    4. group near-duplicates
    5. write XMP sidecars (only with --write-xmp)
    6. report (only with --report)
    7. export the catalog for AI agents (Markdown, JSONL, SQLite)
    8. back up catalog, settings and code to backup_dir (Google Drive)

Stage headers are printed as "=== label ===" and per-photo lines as
"[i/n] OK|FAIL|SKIP ...": the control panel builds its progress bars from them.
"""

from __future__ import annotations

import logging
import os
import time

from . import lmstudio
from .backup import backup
from .classify import Classifier, active_config, given_up, pending_classify
from .config import CONFIG_PATH, Settings
from .db import Catalog
from .dedup import group_duplicates, representatives
from .export import export
from .preprocess import pending_prepare, run_prepare
from .report import build_report, throughput
from .scan import scan
from .taxonomy import Taxonomy, load_taxonomy
from .xmp_writer import write_sidecars

log = logging.getLogger("photomancer.pipeline")


def say(label: str) -> None:
    log.info("=== %s ===", label)


def ensure_server(settings: Settings, setup: bool, parallel: int | None = None) -> None:
    """Start/load the model if asked, then insist that the server offers it."""
    if setup:
        lmstudio.setup(settings.lm, parallel or int(settings.vlm["concurrency"]))
    up, ids = lmstudio.server_up(settings.lm["base_url"])
    if not up:
        log.info("ERROR: LM Studio is not reachable at %s", settings.lm["base_url"])
        raise SystemExit(4)
    if settings.lm["model"] not in ids:
        log.info("ERROR: the server does not offer %s (it offers: %s)",
                 settings.lm["model"], ", ".join(ids) or "nothing")
        raise SystemExit(4)


def classify_shas(settings: Settings, cat: Catalog, tax: Taxonomy, items: list[tuple[str, str]],
                  cfg: dict, label: str, concurrency: int | None = None) -> dict:
    """Tag an explicit list of (sha256, path) with config `cfg` (used by the pilot)."""
    clf = Classifier(settings, cat, tax, cfg, len(items), label, concurrency)
    for sha, path in items:
        clf.submit(sha, path)
    return clf.close()


def run(settings: Settings, *, limit: int = 0, max_minutes: float = 0, retry_failed: bool = False,
        write_xmp: bool = False, lm_setup: bool = True, concurrency: int | None = None,
        do_report: bool = False, prepare_only: bool = False) -> int:
    """Full incremental run. Returns a process exit code."""
    t0 = time.time()
    if not settings.photo_root or not os.path.isdir(settings.photo_root):
        log.info("ERROR: the photo folder is not set or not found: %r - set photo_root in %s "
                 "(or Options in the panel)", settings.photo_root, CONFIG_PATH)
        return 2
    tax = load_taxonomy(settings.taxonomy_path)
    cat = Catalog(settings.db_path)
    log.info("Photos   : %s", settings.photo_root)
    log.info("Data     : %s", settings.data_dir)
    log.info("Model    : %s   taxonomy %s (%s)", settings.lm["model"], tax.version, tax.sha8)
    rc = 0
    try:
        say("scan photo folders")
        scan(settings, cat)
        cfg = active_config(settings, tax)
        max_att = int(settings.vlm["max_attempts"])
        log.info("config %s: %s", cfg["key"], cfg["ident"])
        to_prep = pending_prepare(cat, retry_failed, max_att)
        to_tag = [] if prepare_only else pending_classify(cat, cfg["key"], retry_failed, max_att)
        propagate = bool(settings.dedup.get("propagate"))
        if limit:
            to_tag = to_tag[:limit]
            to_prep = to_prep[:max(0, limit - len(to_tag))]
        log.info("%d photo(s) to prepare, %d prepared photo(s) waiting to be tagged",
                 len(to_prep), len(to_tag))
        deadline = t0 + max_minutes * 60 if max_minutes else None

        if prepare_only or propagate:
            # propagation needs every group before choosing whom to send to the model
            if to_prep:
                say("prepare photos")
                run_prepare(settings, cat, to_prep, tax.palette,
                            should_stop=lambda: bool(deadline and time.time() > deadline))
            if propagate and not prepare_only:
                say("group near-duplicates")
                group_duplicates(cat, int(settings.dedup["hamming_threshold"]))
                reps = representatives(cat)
                to_tag = [r for r in pending_classify(cat, cfg["key"], retry_failed, max_att)
                          if reps.get(r["sha256"], r["sha256"]) == r["sha256"]]
                if limit:
                    to_tag = to_tag[:limit]
                log.info("propagation on: %d representative photo(s) to tag", len(to_tag))
            to_prep = []

        if to_tag or to_prep:
            ensure_server(settings, lm_setup, concurrency)
            say("tag photos")
            clf = Classifier(settings, cat, tax, cfg, len(to_tag) + len(to_prep), "run",
                             concurrency, deadline)
            for r in to_tag:
                clf.submit(r["sha256"], r["path"])

            def on_prepared(res: dict) -> None:
                if res["error"]:
                    clf.skip(res["path"], "unreadable: " + res["error"][:80])
                elif res["sha256"] in clf.submitted or cat.current_result(res["sha256"], cfg["key"]):
                    clf.skip(res["path"], "same content already tagged")
                elif not clf.submit(res["sha256"], res["path"]):
                    clf.skip(res["path"], "time limit reached")

            if to_prep:
                run_prepare(settings, cat, to_prep, tax.palette, on_prepared,
                            should_stop=clf.expired)
            stats = clf.close()
            if stats["failed"]:
                rc = 1 if not stats["ok"] else 0
        elif not prepare_only:
            log.info("nothing new to tag")

        if not propagate:
            say("group near-duplicates")
            group_duplicates(cat, int(settings.dedup["hamming_threshold"]))
        if not prepare_only:
            update_search_index(settings, cat, cfg["key"])
        n_given = given_up(cat, cfg["key"], max_att)
        if n_given:
            log.info("WARNING %d photo(s) given up after %d attempts (tick Retry failed to retry)",
                     n_given, max_att)
        if write_xmp:
            write_sidecars(settings, cat, cfg["key"])
        if do_report:
            build_report(settings, cat, cfg["key"], cfg["ident"])
        if not prepare_only:
            export(settings, cat, cfg["key"])
            cat.close()                     # the snapshot reads the file itself
            backup(settings)
        return rc
    finally:
        cat.close()
        say(f"pipeline finished in {(time.time() - t0) / 60:.1f} min")


def update_search_index(settings: Settings, cat: Catalog, key: str) -> None:
    """Embed newly tagged photos for semantic search (never fails the run)."""
    say("update search index")
    try:
        up, _ids = lmstudio.server_up(settings.lm["base_url"])
        if not up and settings.lm["lms_path"]:
            lmstudio._run([settings.lm["lms_path"], "server", "start"], "start LM Studio server")
        from .search import update_index
        update_index(settings, cat, key)
    except Exception as exc:
        log.info("WARNING search index not updated (%s: %s) - search falls back to exact words "
                 "until the next run", type(exc).__name__, exc)


def plan(settings: Settings) -> dict:
    """What a run would do - scans, changes nothing else, calls no model."""
    tax = load_taxonomy(settings.taxonomy_path)
    cat = Catalog(settings.db_path)
    try:
        say("scan photo folders")
        s = scan(settings, cat)
        cfg = active_config(settings, tax)
        max_att = int(settings.vlm["max_attempts"])
        prep = pending_prepare(cat, False, max_att)
        tag = pending_classify(cat, cfg["key"], False, max_att)
        out = status_counts(cat, cfg["key"], max_att)
        out.update({"to_prepare": len(prep), "prepared_untagged": len(tag), "scan": s})
        say("plan")
        log.info("config %s  %s", cfg["key"], cfg["ident"])
        log.info("%d JPEG(s) present; %d prepared; %d tagged with this configuration",
                 out["files"], out["prepared"], out["tagged"])
        log.info("next run: prepare %d photo(s), tag about %d photo(s)",
                 len(prep), len(tag) + len(prep))
        log.info("given up: %d; XMP sidecars written: %d", out["given_up"], out["xmp"])
        tp = throughput(cat, cfg["key"])
        if tp["per_min"]:
            n = len(tag) + len(prep)
            log.info("measured %.1f photos/min -> about %.1f h for that", tp["per_min"],
                     n / tp["per_min"] / 60)
        for r in (tag or prep)[:10]:
            log.info("  %s", r["path"])
        return out
    finally:
        cat.close()


def status_counts(cat: Catalog, key: str, max_attempts: int) -> dict:
    """Cheap counts for the panel's tiles (no scan)."""
    return {
        "files": cat.one("SELECT COUNT(*) FROM files WHERE missing=0") or 0,
        "prepared": cat.one("SELECT COUNT(*) FROM files f JOIN stage_status s ON s.file_id=f.file_id "
                            "AND s.stage='prepare' AND s.status='ok' WHERE f.missing=0") or 0,
        "tagged": cat.one("SELECT COUNT(*) FROM files f WHERE f.missing=0 AND EXISTS (SELECT 1 FROM "
                          "vlm_results v WHERE v.sha256=f.sha256 AND v.config_key=? AND "
                          "v.status='ok')", (key,)) or 0,
        "given_up": given_up(cat, key, max_attempts),
        "xmp": cat.one("SELECT COUNT(*) FROM xmp_written") or 0,
    }

"""Command line: python -m photomancer <command> [options]   (no command = the panel)

    run        scan + prepare + tag new photos   [--limit N] [--max-minutes M]
               [--retry-failed] [--write-xmp] [--report] [--concurrency C] [--no-lm-setup]
    plan       what `run` would do (scan only, no model)
    check      environment check on 3 random photos
    prepare    scan + CPU stage only (no model)
    dedup      recompute near-duplicate groups
    write      write XMP sidecars   [--dry-run] [--force] [--limit N]
    unwrite    delete the sidecars this tool wrote   [--dry-run] --yes
    report     statistics + CSV + HTML contact sheet
    index      update the semantic search index
    search     "cat" [--folders --level N]
    export     catalog for AI agents (Markdown + SQLite) into <data>/export
    curate     "theme" [--top N] [--pool M]   the model picks the best photos
    backup     catalog snapshot + settings + code to backup_dir (also after every run)
    pilot      sample | run | ab | bench | template | eval | repeat   (see pilot.py)
    scheduled  <command ...>   run a command with its output appended to
               logs/run_scheduled.log (what the weekly/monthly task runs)
    unschedule remove the weekly / monthly task
    shortcuts  [--desktop] [--menu] [--remove]   desktop icon / Start-menu entry
    setup      first-run wizard (folders, LM Studio check, shortcuts)
    gui        the control panel (default)

Global options: --root DIR (photo folder), --data DIR (database/cache folder).
"""

from __future__ import annotations

import argparse
import os
import sys


def _app_commands(argv: list[str]) -> int | None:
    """Commands shared in form with BiblioMancer (None = not one of them)."""
    if not argv or argv[0] == "gui":
        from .gui import main as gui_main
        gui_main()
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "scheduled":
        from ._shared import paths
        from ._shared.scheduled import run_logged
        from .config import PKG_FILE, load_settings
        child = [*paths.self_command("photomancer", PKG_FILE), *rest]
        return run_logged(child, load_settings().log_dir, paths.child_env(PKG_FILE), " ".join(rest))
    if cmd == "shortcuts":
        from .gui import make_shortcuts
        return make_shortcuts(desktop="--desktop" in rest, menu="--menu" in rest or
                              "--desktop" not in rest, remove="--remove" in rest)
    if cmd == "setup":
        from .gui import run_wizard
        return run_wizard()
    if cmd == "unschedule":                      # also run by the uninstaller
        from ._shared import schedule
        from .gui import SPEC
        ok, msg = schedule.remove(SPEC.task_name)
        print(msg)
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    rc = _app_commands(argv)
    if rc is not None:
        return rc
    ap = argparse.ArgumentParser(prog="photomancer", description="Tag a JPEG archive with a local VLM.")
    ap.add_argument("--root", help="photo folder (overrides config.yaml)")
    ap.add_argument("--data", help="data folder (overrides config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="scan, prepare and tag new photos")
    r.add_argument("--limit", type=int, default=0, help="at most N photos this run")
    r.add_argument("--max-minutes", type=float, default=0)
    r.add_argument("--retry-failed", action="store_true")
    r.add_argument("--concurrency", type=int, default=None)
    r.add_argument("--no-lm-setup", action="store_true",
                   help="assume LM Studio already serves the model")
    r.add_argument("--write-xmp", action="store_true", help="write sidecars after tagging")
    r.add_argument("--report", action="store_true")

    sub.add_parser("plan", help="what a run would do (changes nothing)")
    c = sub.add_parser("check", help="G1 environment check")
    c.add_argument("--images", type=int, default=3)
    c.add_argument("--seed", type=int, default=0)
    pr = sub.add_parser("prepare", help="scan + CPU stage only")
    pr.add_argument("--limit", type=int, default=0)
    pr.add_argument("--retry-failed", action="store_true")
    sub.add_parser("dedup", help="recompute near-duplicate groups")
    w = sub.add_parser("write", help="write XMP sidecars for tagged photos")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--force", action="store_true", help="also overwrite foreign/edited sidecars")
    w.add_argument("--limit", type=int, default=0)
    u = sub.add_parser("unwrite", help="delete sidecars written by this tool")
    u.add_argument("--dry-run", action="store_true")
    u.add_argument("--yes", action="store_true")
    sub.add_parser("report", help="statistics, CSV and HTML contact sheet")
    sub.add_parser("backup", help="back up catalog, settings and code to backup_dir")
    sub.add_parser("export", help="catalog for AI agents (Markdown, JSONL, SQLite)")
    cu = sub.add_parser("curate", help="the model picks the best photos for a theme")
    cu.add_argument("theme")
    cu.add_argument("--top", type=int, default=12)
    cu.add_argument("--pool", type=int, default=48, help="candidates the model looks at")
    cu.add_argument("--no-lm-setup", action="store_true")
    rb = sub.add_parser("rebuild", help="back up, then re-tag every photo with the current settings")
    rb.add_argument("--yes", action="store_true")
    rb.add_argument("--max-minutes", type=float, default=0)
    rb.add_argument("--write-xmp", action="store_true")
    sub.add_parser("logdir", help="print the log folder (used by run_scheduled.cmd)")
    sub.add_parser("index", help="update the semantic search index")
    se = sub.add_parser("search", help="semantic search: photos, or folders with --folders")
    se.add_argument("query")
    se.add_argument("--folders", action="store_true")
    se.add_argument("--level", type=int, default=0, help="folders: 0 = own folder, 1 = year, 2 = album")
    se.add_argument("--top", type=int, default=15)

    pi = sub.add_parser("pilot", help="pilot protocol (gates G2-G4)")
    pi.add_argument("action", choices=["sample", "run", "ab", "bench", "template", "eval", "repeat"])
    pi.add_argument("--n", type=int, default=None, help="photos (sample: 100, others: 30)")
    pi.add_argument("--seed", type=int, default=42)
    pi.add_argument("--replace", action="store_true", help="sample: discard the existing pilot")
    pi.add_argument("--gt", help="eval: filled-in ground-truth CSV")
    pi.add_argument("--concurrency", type=int, default=None)
    pi.add_argument("--levels", default="1,2,4", help="bench: concurrency levels")
    pi.add_argument("--limit", type=int, default=0)
    pi.add_argument("--retry-failed", action="store_true")

    a = ap.parse_args(argv)
    os.environ.setdefault("NUMEXPR_MAX_THREADS", str(os.cpu_count() or 8))
    if a.root:
        os.environ["PHOTOMANCER_ROOT"] = a.root
    if a.data:
        os.environ["PHOTOMANCER_DATA"] = a.data

    from .config import load_settings, setup_logging
    s = load_settings()
    if a.cmd == "logdir":                       # plain output, no logging set-up
        print(s.log_dir)
        return 0
    log = setup_logging(s)
    try:
        return _dispatch(a, s)
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        log.info("interrupted - everything finished so far is saved")
        return 130
    except Exception as exc:  # report, do not hide
        log.exception("ERROR %s: %s", type(exc).__name__, exc)
        return 1


def _dispatch(a: argparse.Namespace, s) -> int:
    from . import pipeline
    from .classify import active_config
    from .db import Catalog
    from .taxonomy import load_taxonomy

    if a.cmd == "run":
        return pipeline.run(s, limit=a.limit, max_minutes=a.max_minutes,
                            retry_failed=a.retry_failed, write_xmp=a.write_xmp,
                            lm_setup=not a.no_lm_setup, concurrency=a.concurrency,
                            do_report=a.report)
    if a.cmd == "plan":
        pipeline.plan(s)
        return 0
    if a.cmd == "check":
        from .check import run_check
        return run_check(s, a.images, a.seed)
    if a.cmd == "prepare":
        return pipeline.run(s, limit=a.limit, retry_failed=a.retry_failed, prepare_only=True)
    if a.cmd == "backup":
        from .backup import backup
        return 0 if backup(s) else 1
    if a.cmd == "curate":
        from .curate import curate
        return curate(s, a.theme, a.top, a.pool, lm_setup=not a.no_lm_setup)
    if a.cmd == "export":
        from .export import export
        tax = load_taxonomy(s.taxonomy_path)
        cat = Catalog(s.db_path)
        try:
            export(s, cat, active_config(s, tax)["key"])
        finally:
            cat.close()
        return 0
    if a.cmd == "rebuild":
        if not a.yes:
            print("Refusing without --yes: every photo is tagged again (hours).")
            return 2
        from .backup import backup
        from .db import now_iso
        tax = load_taxonomy(s.taxonomy_path)
        key = active_config(s, tax)["key"]
        pipeline.say("back up, then set the current results aside")
        backup(s)
        cat = Catalog(s.db_path)
        # results are never deleted: the old ones stay in the catalog under another key
        cat.x("UPDATE vlm_results SET config_key = config_key || ':superseded:' || ? "
              "WHERE config_key = ?", (now_iso(), key))
        cat.x("DELETE FROM search_index WHERE config_key = ?", (key,))
        cat.close()
        return pipeline.run(s, max_minutes=a.max_minutes, write_xmp=a.write_xmp)
    if a.cmd == "dedup":
        from .dedup import group_duplicates
        cat = Catalog(s.db_path)
        group_duplicates(cat, int(s.dedup["hamming_threshold"]))
        return 0
    if a.cmd in ("write", "report"):
        tax = load_taxonomy(s.taxonomy_path)
        cfg = active_config(s, tax)
        cat = Catalog(s.db_path)
        if a.cmd == "write":
            from .xmp_writer import write_sidecars
            c = write_sidecars(s, cat, cfg["key"], dry_run=a.dry_run, force=a.force, limit=a.limit)
            return 1 if c["errors"] else 0
        from .report import build_report
        build_report(s, cat, cfg["key"], cfg["ident"])
        return 0
    if a.cmd in ("index", "search"):
        tax = load_taxonomy(s.taxonomy_path)
        cfg = active_config(s, tax)
        cat = Catalog(s.db_path)
        if a.cmd == "index":
            pipeline.update_search_index(s, cat, cfg["key"])
            return 0
        from .search import SearchIndex, relevant, search_folders
        idx = SearchIndex(s, cat, cfg["key"])
        paths: dict[str, list[str]] = {}
        for r in cat.q("SELECT sha256, path FROM files WHERE missing=0 AND sha256 IS NOT NULL "
                       "ORDER BY path"):
            paths.setdefault(r["sha256"], []).append(r["path"])
        if a.folders:
            for d in search_folders(idx, paths, a.query, a.level)[:a.top]:
                print(f"{d['score']:.3f}  {d['hits']:4d}/{d['photos']:<5d} {d['folder']}")
        else:
            sc = idx.scores(a.query)
            hit = relevant(sc)
            for i in sorted(range(len(sc)), key=lambda i: -sc[i])[:a.top]:
                print(f"{sc[i]:.3f} {'*' if hit[i] else ' '} {paths.get(idx.shas[i], ['?'])[0]}")
        return 0
    if a.cmd == "unwrite":
        from .xmp_writer import remove_sidecars
        if not (a.yes or a.dry_run):
            print("Refusing without --yes (or use --dry-run to see what would go).")
            return 2
        remove_sidecars(s, Catalog(s.db_path), dry_run=a.dry_run)
        return 0
    if a.cmd == "pilot":
        from . import pilot
        if a.action == "sample":
            return pilot.make_sample(s, a.n or 100, a.seed, a.replace)
        if a.action == "run":
            return pilot.run_pilot(s, limit=a.limit, retry_failed=a.retry_failed,
                                   concurrency=a.concurrency)
        if a.action == "ab":
            return pilot.ab_test(s, a.n or 30, a.seed, a.concurrency or 1)
        if a.action == "bench":
            return pilot.bench(s, a.n or 30, a.seed, tuple(int(x) for x in a.levels.split(",")))
        if a.action == "template":
            return pilot.template(s)
        if a.action == "eval":
            if not a.gt:
                print("pilot eval needs --gt <filled-in CSV>")
                return 2
            return pilot.evaluate(s, a.gt)
        if a.action == "repeat":
            return pilot.repeat(s, a.n or 30, a.seed, a.concurrency or 2)
    return 2


if __name__ == "__main__":
    sys.exit(main())

"""Backup of everything irreplaceable to a synced folder (Google Drive).

Same idea as BiblioMancer's pipeline_backup: the working copy
lives on local disk (SQLite on the Drive mount can be locked mid-run), so
after every run a consistent snapshot of the catalog plus the settings and
code goes to `backup_dir`. The image cache is not copied: it is rebuilt from
the photos.

    <backup_dir>/
        catalog.db          consistent snapshot (sqlite backup API)
        catalog.prev.db     the snapshot before it, in case a bad run is backed up
        config.yaml, taxonomy.yaml, theme.css, <custom prompt>
        code/               photomancer, tests, tools, assets, launchers, README
        backup_manifest.json
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3

from .config import APP_HOME, CONFIG_DIR, CONFIG_PATH, Settings
from .db import now_iso

log = logging.getLogger("photomancer.backup")

CODE_DIRS = ("src", "tests", "tools")
CODE_FILES = ("PhotoMancer.pyw", "PhotoMancer.cmd", "README.md", "requirements.txt",
              "pyproject.toml", ".gitignore")


def _mirror(src: str, dst: str) -> int:
    """Copy new/changed files from src to dst and drop files gone from src."""
    n = 0
    keep = set()
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache")]
        rel = os.path.relpath(base, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            s, d = os.path.join(base, f), os.path.normpath(os.path.join(dst, rel, f))
            keep.add(d)
            if not os.path.exists(d) or os.path.getmtime(s) > os.path.getmtime(d) + 1 \
                    or os.path.getsize(s) != os.path.getsize(d):
                shutil.copy2(s, d)
                n += 1
    for base, _dirs, files in os.walk(dst):
        for f in files:
            p = os.path.normpath(os.path.join(base, f))
            if p not in keep:
                os.remove(p)
    return n


def backup(settings: Settings, force: bool = False) -> bool:
    """Write the backup; never fails the run (returns False and logs instead)."""
    dest = settings.backup_dir
    if not dest:
        log.info("backup skipped: no backup_dir in config.yaml")
        return False
    log.info("=== back up to %s ===", dest)
    try:                                 # a backup folder belongs to one catalog
        with open(os.path.join(dest, "backup_manifest.json"), encoding="utf-8") as fh:
            owner = json.load(fh).get("source_data_dir") or ""
    except (OSError, ValueError):
        owner = ""
    if owner and os.path.normcase(os.path.abspath(owner)) != os.path.normcase(
            os.path.abspath(settings.data_dir)) and not force:
        log.info("WARNING backup not written: %s holds the backup of another catalog (%s)",
                 dest, owner)
        return False
    root = os.path.normcase(os.path.abspath(settings.photo_root))
    here = os.path.normcase(os.path.abspath(dest))
    if here == root or (here.startswith(root + os.sep) and not
                        set(os.path.relpath(here, root).lower().split(os.sep)) & settings.exclude_dirs):
        # its catalog, code and icons would be scanned and tagged as photos
        log.info("WARNING backup not written: %s is inside photo_root; put it in a subfolder "
                 "listed in exclude_dirs (config.yaml)", dest)
        return False
    try:
        os.makedirs(dest, exist_ok=True)
        tmp = os.path.join(settings.data_dir, "catalog_backup.tmp.db")
        src = sqlite3.connect(settings.db_path)
        dst = sqlite3.connect(tmp)
        src.backup(dst)                          # consistent snapshot, never a torn copy
        dst.close()
        src.close()
        final = os.path.join(dest, "catalog.db")
        if os.path.exists(final):
            shutil.copy2(final, os.path.join(dest, "catalog.prev.db"))
        shutil.copy2(tmp, final)
        os.remove(tmp)

        extras = [CONFIG_PATH, settings.taxonomy_path, os.path.join(CONFIG_DIR, "theme.css")]
        if settings.prompt_path:
            extras.append(settings.prompt_path)
        for p in extras:
            if p and os.path.exists(p):
                shutil.copy2(p, os.path.join(dest, os.path.basename(p)))

        code = os.path.join(dest, "code")
        changed = 0
        for d in CODE_DIRS:
            if os.path.isdir(os.path.join(APP_HOME, d)):
                changed += _mirror(os.path.join(APP_HOME, d), os.path.join(code, d))
        for f in CODE_FILES:
            if os.path.exists(os.path.join(APP_HOME, f)):
                shutil.copy2(os.path.join(APP_HOME, f), os.path.join(code, f))

        size = os.path.getsize(final)
        with open(os.path.join(dest, "backup_manifest.json"), "w", encoding="utf-8") as fh:
            json.dump({"backed_up_at": now_iso(), "catalog_bytes": size,
                       "source_app_home": APP_HOME, "source_data_dir": settings.data_dir},
                      fh, indent=2)
        log.info("backup written: catalog.db %.1f MB, settings, code (%d file(s) updated)",
                 size / 1e6, changed)
        return True
    except Exception as exc:  # a full Drive or a locked file must not fail the run
        log.info("WARNING backup not written (%s: %s) - the next run tries again",
                 type(exc).__name__, exc)
        return False

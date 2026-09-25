"""Settings: config.yaml merged over built-in defaults, plus derived paths
(same scheme as BiblioMancer).

Where config.yaml lives (_shared.paths.find_config):
  1. PHOTOMANCER_CONFIG environment variable
  2. next to the code (portable checkout; how this PC has always run)
  3. the per-user config folder (%APPDATA%\\PhotoMancer, ~/Library/...,
     ~/.config/photomancer) - installed apps
Relative paths inside it (taxonomy, prompt) are resolved against its folder.
A missing taxonomy.yaml is copied there from the package default.

Two more environment variables let the pilot and the tests run on other data
without editing the file:

    PHOTOMANCER_ROOT   overrides photo_root
    PHOTOMANCER_DATA   overrides data_dir
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any

from ._shared import paths
from ._shared.lmstudio import DEFAULT_BASE_URL, find_lms
from ._shared.settings import load_yaml, merge, update_yaml

APP_NAME = "PhotoMancer"
PKG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__init__.py")
APP_HOME = paths.app_home(PKG_FILE)
CONFIG_PATH, PORTABLE = paths.find_config(APP_NAME, PKG_FILE, "config.yaml", "PHOTOMANCER_CONFIG")
CONFIG_DIR = os.path.dirname(CONFIG_PATH)
TEMPLATE_PATH = paths.resource(PKG_FILE, "assets", "config.example.yaml")
DEFAULT_TAXONOMY = paths.resource(PKG_FILE, "assets", "taxonomy.yaml")

DEFAULTS: dict[str, Any] = {
    "photo_root": "",
    "data_dir": "",
    "backup_dir": "",
    "exclude_dirs": ["photomancer_data", "photomancer_backup", ".tmp.drivedownload",
                     ".tmp.driveupload", "$RECYCLE.BIN"],
    "taxonomy_file": "taxonomy.yaml",
    "prompt_file": "",
    "lmstudio": {"base_url": DEFAULT_BASE_URL, "lms_path": "",
                 "model": "qwen/qwen3.5-9b", "context_per_request": 4096, "ttl_s": 3600,
                 "reasoning_effort": "none",
                 "embedding_model": "text-embedding-nomic-embed-text-v1.5"},
    "vlm": {"image_side": 1024, "exif_context": True, "temperature": 0.1, "max_tokens": 600,
            "concurrency": 2, "timeout_s": 180, "http_retries": 3, "max_attempts": 3,
            "jpeg_quality": 90},
    "preprocess": {"workers": max(2, min(12, (os.cpu_count() or 4) - 2)), "cache_side": 1024,
                   "thumb_side": 256, "thumb_quality": 85},
    "exif": {"long_exposure_s": 1.0, "high_iso": 1600, "telephoto_35mm": 200,
             "crop_factors": {}},
    "colors": {"k": 5, "sample_pixels": 20000, "seed": 42, "mono_chroma_p95": 10.0,
               "low_key_mean_L": 30, "high_key_mean_L": 70, "key_clip_share": 0.05},
    "dedup": {"hamming_threshold": 6, "propagate": False},
    "xmp": {"root": "AI", "max_colors": 3, "min_color_fraction": 0.12,
            "keywords_as_tags": True},
    "report": {"low_confidence": 0.5, "sample_per_genre": 12, "seed": 42},
}

_merge = merge          # kept for older callers


def _abs(path: str) -> str:
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(CONFIG_DIR, path))


def default_data_dir() -> str:
    return os.path.join(paths.documents_dir(), f"{APP_NAME} data")


@dataclass
class Settings:
    """Parsed configuration with resolved paths."""

    raw: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        c = self.raw
        root = os.environ.get("PHOTOMANCER_ROOT") or c["photo_root"]
        self.photo_root = os.path.normpath(root) if root else ""      # empty = not set up yet
        self.data_dir = os.path.normpath(os.environ.get("PHOTOMANCER_DATA") or c["data_dir"]
                                         or default_data_dir())
        self.exclude_dirs = {d.lower() for d in c.get("exclude_dirs") or []}
        self.backup_dir = os.path.normpath(c["backup_dir"]) if c.get("backup_dir") else ""
        self.taxonomy_path = _abs(c["taxonomy_file"])
        self.prompt_path = _abs(c["prompt_file"]) if c.get("prompt_file") else ""
        self.lm = c["lmstudio"]
        self.lm["lms_path"] = find_lms(self.lm.get("lms_path") or "")
        self.vlm = c["vlm"]
        self.pre = c["preprocess"]
        self.exif = c["exif"]
        self.colors = c["colors"]
        self.dedup = c["dedup"]
        self.xmp = c["xmp"]
        self.report = c["report"]
        if not os.path.exists(self.taxonomy_path) and os.path.exists(DEFAULT_TAXONOMY):
            try:                                   # first run of an installed app
                os.makedirs(os.path.dirname(self.taxonomy_path), exist_ok=True)
                shutil.copy2(DEFAULT_TAXONOMY, self.taxonomy_path)
            except OSError:
                self.taxonomy_path = DEFAULT_TAXONOMY
        self.derive_paths()

    def derive_paths(self) -> None:
        """(Re)compute every path that hangs off data_dir."""
        d = self.data_dir
        self.db_path = os.path.join(d, "catalog.db")
        self.img_cache = os.path.join(d, "cache", "img")
        self.thumb_cache = os.path.join(d, "cache", "thumb")
        self.raw_dir = os.path.join(d, "cache", "raw_responses")
        self.log_dir = os.path.join(d, "logs")
        self.report_dir = os.path.join(d, "reports")
        self.pilot_dir = os.path.join(d, "pilot")
        self.export_dir = os.path.join(d, "export")

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.img_cache, self.thumb_cache, self.raw_dir,
                  self.log_dir, self.report_dir):
            os.makedirs(p, exist_ok=True)

    def cache_path(self, sha: str, kind: str = "img") -> str:
        base = self.img_cache if kind == "img" else self.thumb_cache
        return os.path.join(base, sha[:2], sha + ".jpg")


def load_settings(path: str | None = None) -> Settings:
    """Read config.yaml (missing file = defaults; a bad file is reported, not fatal)."""
    return Settings(load_yaml(path or CONFIG_PATH, DEFAULTS))


def template() -> str:
    try:
        with open(TEMPLATE_PATH, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def update_config(changes: dict, path: str | None = None) -> None:
    """Write `key` or `section.key` values into config.yaml, keeping comments
    (a missing file is started from the commented template)."""
    update_yaml(path or CONFIG_PATH, changes, template())


def exists() -> bool:
    return os.path.exists(CONFIG_PATH)


def setup_logging(settings: Settings, name: str = "photomancer") -> logging.Logger:
    """Log to stdout (read live by the control panel) and to logs/<name>.log."""
    settings.ensure_dirs()
    root = logging.getLogger()
    if getattr(root, "_photomancer", False):
        return logging.getLogger(name)
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.FileHandler(os.path.join(settings.log_dir, "photomancer.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    for noisy in ("urllib3", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._photomancer = True  # type: ignore[attr-defined]
    return logging.getLogger(name)

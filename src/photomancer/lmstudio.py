"""LM Studio control for the tagger: a thin layer over _shared/lmstudio.py
(the same code BiblioMancer uses), with the tagger's needs baked
in - the model must have vision, and output goes to the photomancer logger.
"""

from __future__ import annotations

import logging

from ._shared import lmstudio as _lm
from ._shared.lmstudio import gpu_memory_mb, installed_models, loaded_models, server_up  # noqa: F401

log = logging.getLogger("photomancer.lmstudio")


def model_info(lms: str, name: str) -> dict | None:
    return next((m for m in installed_models(lms, None) or []
                 if m.get("modelKey", "").lower() == name.strip().lower()), None)


def check_model(lms: str, name: str, context_length: int) -> tuple[bool, str]:
    """Is `name` an installed vision chat model? Returns (ok, message)."""
    return _lm.check_model(lms, name, context_length, need_vision=True)


def _run(argv: list[str], label: str) -> int:
    return _lm.run_lms(argv[0], argv[1:], label, out=log.info)


def setup(lm: dict, parallel: int = 1) -> None:
    """Start the server and (re)load the model at context_per_request x parallel."""
    per = int(lm.get("context_per_request") or lm.get("context_length") or 4096)
    _lm.setup(lm["lms_path"], lm["model"], per, parallel=parallel, ttl_s=int(lm.get("ttl_s", 3600)),
              need_vision=True, out=log.info)

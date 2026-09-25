"""Stage 3 - classify (GPU, through LM Studio) with N concurrent requests.

Every result row records the run and the `config_key`: a hash of everything
that changes the model's answer (model, quant, prompt version, taxonomy
version + content hash, image size, EXIF context, sampling). A photo counts
as tagged only when it has an ok row for the ACTIVE key, so changing any of
these re-queues photos instead of silently mixing configurations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import lmstudio
from .config import Settings
from .db import Catalog, now_iso
from .exif import exif_summary
from .prompt import build_system_prompt, user_text
from .taxonomy import Taxonomy
from .vlm_client import VlmClient

log = logging.getLogger("photomancer.classify")


def active_config(settings: Settings, tax: Taxonomy, tag: str = "",
                  quant: str | None = None) -> dict:
    """The configuration that identifies a result set, plus its key."""
    system, pver = build_system_prompt(tax, settings.prompt_path)
    if quant is None:
        info = lmstudio.model_info(settings.lm["lms_path"], settings.lm["model"]) or {}
        quant = (info.get("quantization") or {}).get("name", "?")
    ident = {
        "model": settings.lm["model"], "quant": quant, "prompt_version": pver,
        "taxonomy_version": tax.version, "taxonomy_sha8": tax.sha8,
        "image_side": int(settings.vlm["image_side"]),
        "exif_context": bool(settings.vlm["exif_context"]),
        "temperature": float(settings.vlm["temperature"]),
        "reasoning_effort": settings.lm.get("reasoning_effort"),
        "tag": tag,
    }
    key = hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:12]
    return {"key": key, "ident": ident, "system": system, "prompt_version": pver, "quant": quant}


def pending_classify(cat: Catalog, key: str, retry_failed: bool = False,
                     max_attempts: int = 3) -> list:
    """Prepared contents (one row per sha256) without an ok result for `key`."""
    return cat.q(
        "SELECT f.sha256, MIN(f.path) AS path FROM files f "
        "JOIN stage_status s ON s.file_id = f.file_id AND s.stage = 'prepare' AND s.status = 'ok' "
        "WHERE f.missing = 0 AND f.sha256 IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM vlm_results v WHERE v.sha256 = f.sha256 "
        "                AND v.config_key = ? AND v.status = 'ok') "
        "AND (? OR (SELECT COUNT(*) FROM vlm_results v WHERE v.sha256 = f.sha256 "
        "           AND v.config_key = ? AND v.status = 'failed') < ?) "
        "GROUP BY f.sha256 ORDER BY path",
        (key, 1 if retry_failed else 0, key, max_attempts))


def given_up(cat: Catalog, key: str, max_attempts: int) -> int:
    return cat.one(
        "SELECT COUNT(*) FROM (SELECT v.sha256 FROM vlm_results v WHERE v.config_key = ? "
        "GROUP BY v.sha256 HAVING SUM(v.status = 'ok') = 0 AND SUM(v.status = 'failed') >= ?)",
        (key, max_attempts)) or 0


class Classifier:
    """Thread pool that classifies photos and writes each result as it lands."""

    def __init__(self, settings: Settings, cat: Catalog, tax: Taxonomy, cfg: dict,
                 total: int, label: str = "", concurrency: int | None = None,
                 deadline: float | None = None):
        self.s, self.cat, self.cfg = settings, cat, cfg
        self.client = VlmClient(settings.lm, settings.vlm, tax, cfg["system"])
        self.run_id = cat.new_run(cfg["key"], settings.lm["model"], cfg["quant"],
                                  cfg["prompt_version"], tax.version,
                                  json.dumps(dict(cfg["ident"], concurrency=concurrency or
                                                  settings.vlm["concurrency"],
                                                  max_tokens=settings.vlm["max_tokens"])),
                                  label)
        n = int(concurrency or settings.vlm["concurrency"])
        self.pool = ThreadPoolExecutor(max_workers=max(1, min(8, n)))
        self.lock = threading.Lock()
        self.total, self.done, self.n_ok, self.n_fail = total, 0, 0, 0
        self.submitted: set[str] = set()
        self.deadline = deadline
        self.t0 = time.time()
        self.latencies: list[float] = []

    def expired(self) -> bool:
        return self.deadline is not None and time.time() > self.deadline

    def submit(self, sha: str, path: str) -> bool:
        """Queue one content hash (ignored if already queued or past the deadline)."""
        with self.lock:
            if sha in self.submitted or self.expired():
                return False
            self.submitted.add(sha)
        self.pool.submit(self._work, sha, path)
        return True

    def skip(self, path: str, why: str) -> None:
        """Count a photo that needs no model call (keeps [i/n] consistent)."""
        with self.lock:
            self.done += 1
            i = self.done
        log.info("[%d/%d] SKIP %s | %s", i, self.total, why, path)

    def _work(self, sha: str, path: str) -> None:
        if self.expired():
            self.skip(path, "time limit reached")
            return
        e = self.cat.one("SELECT * FROM exif WHERE sha256=?", (sha,))
        exif_line = exif_summary(dict(e)) if (e is not None and self.s.vlm["exif_context"]) else ""
        ans = self.client.classify(self.s.cache_path(sha), user_text(exif_line))
        d = ans.data or {}
        with self.cat.lock:
            cur = self.cat.conn.execute(
                "INSERT INTO vlm_results(sha256, run_id, config_key, json, primary_genre, subgenre, "
                "confidence, status, error, warnings, latency_s, tokens_in, tokens_out, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sha, self.run_id, self.cfg["key"],
                 json.dumps(d, ensure_ascii=False) if ans.ok else None,
                 d.get("primary_genre"), d.get("subgenre"), d.get("genre_confidence"),
                 "ok" if ans.ok else "failed", ans.error,
                 json.dumps(ans.warnings, ensure_ascii=False) if ans.warnings else None,
                 round(ans.latency_s, 3), ans.tokens_in, ans.tokens_out, now_iso()))
            self.cat.conn.commit()
            rid = cur.lastrowid
        if not ans.ok:
            try:
                with open(os.path.join(self.s.raw_dir, f"{sha[:16]}_{rid}.json"), "w",
                          encoding="utf-8") as fh:
                    json.dump({"path": path, "error": ans.error, "responses": ans.raw}, fh,
                              ensure_ascii=False, indent=1)
            except OSError:
                pass
        with self.lock:
            self.done += 1
            i = self.done
            if ans.ok:
                self.n_ok += 1
                self.latencies.append(ans.latency_s)
            else:
                self.n_fail += 1
        if ans.ok:
            extra = " (repaired)" if any(w.startswith("repaired") for w in ans.warnings) else ""
            think = f" think={ans.reasoning_tokens}" if ans.reasoning_tokens else ""
            log.info("[%d/%d] OK %.1fs %s/%s %.2f tok=%d/%d%s%s | %s", i, self.total,
                     ans.latency_s, d["primary_genre"], d["subgenre"], d["genre_confidence"],
                     ans.tokens_in, ans.tokens_out, think, extra, path)
        else:
            log.info("[%d/%d] FAIL %.1fs %s | %s", i, self.total, ans.latency_s, ans.error, path)

    def close(self) -> dict:
        """Wait for queued work, close the run row, return throughput figures."""
        self.pool.shutdown(wait=True)
        wall = time.time() - self.t0
        self.cat.finish_run(self.run_id, self.n_ok, self.n_fail, wall)
        lat = sorted(self.latencies)
        pct = lambda p: lat[min(len(lat) - 1, int(p * len(lat)))] if lat else 0.0  # noqa: E731
        out = {"run_id": self.run_id, "ok": self.n_ok, "failed": self.n_fail, "wall_s": wall,
               "per_min": self.n_ok / wall * 60 if wall > 0 else 0.0,     # successes only
               "p50": pct(0.5), "p95": pct(0.95)}
        if self.n_ok or self.n_fail:
            log.info("tagged %d, failed %d in %.1f min - %.1f photos/min, latency p50 %.1fs p95 %.1fs",
                     self.n_ok, self.n_fail, wall / 60, out["per_min"], out["p50"], out["p95"])
        return out

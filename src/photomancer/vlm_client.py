"""LM Studio chat-completions client for one photo: retries, timing, repair.

Flow per photo:
    request (HTTP errors -> exponential backoff, `http_retries` times)
    -> parse + validate (strict)
    -> on invalid answer: one repair request quoting the reason
    -> validate (lenient) -> ok | failed (raw answer kept for inspection)
"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from dataclasses import dataclass, field

import requests
from PIL import Image

from .prompt import REPAIR
from .schema import json_schema, validate_result
from .taxonomy import Taxonomy

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


@dataclass
class Answer:
    """Outcome of classifying one photo."""

    ok: bool
    data: dict | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    reasoning_tokens: int = 0
    raw: list = field(default_factory=list)        # every response body, for audit


def encode_image(path: str, side: int, quality: int = 90) -> str:
    """Cached JPEG -> data URL, downscaled if `side` is below the cache size."""
    with Image.open(path) as im:
        if max(im.size) <= side:
            with open(path, "rb") as fh:
                data = fh.read()
        else:
            im = im.convert("RGB")
            im.thumbnail((side, side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
            data = buf.getvalue()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def strip_to_json(content: str) -> str:
    content = _THINK_RE.sub("", content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
    a, b = content.find("{"), content.rfind("}")
    return content[a:b + 1] if a != -1 and b > a else content


class VlmClient:
    """Stateless apart from its settings; safe to share between threads."""

    def __init__(self, lm: dict, vlm: dict, tax: Taxonomy, system_prompt: str):
        self.base = lm["base_url"].rstrip("/")
        self.model = lm["model"]
        self.reasoning_effort = lm.get("reasoning_effort")
        self.vlm = vlm
        self.tax = tax
        self.system = system_prompt
        self.schema = json_schema(tax)
        self.session = requests.Session()

    def _post(self, messages: list, ans: Answer) -> tuple[str, str]:
        """One completion with backoff on transport errors. Returns (content, finish)."""
        payload = {
            "model": self.model, "messages": messages, "stream": False,
            "temperature": float(self.vlm["temperature"]),
            "max_tokens": int(self.vlm["max_tokens"]),
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "photo_record", "strict": True, "schema": self.schema}},
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        tries = int(self.vlm.get("http_retries", 3))
        delay = 2.0
        for attempt in range(tries + 1):
            try:
                r = self.session.post(f"{self.base}/chat/completions", json=payload,
                                      timeout=float(self.vlm["timeout_s"]))
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code != 200:            # 4xx: retrying the same request is pointless
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
                body = r.json()
                break
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                if attempt == tries:
                    raise RuntimeError(f"request failed after {tries + 1} tries: {exc}") from None
                time.sleep(delay)
                delay *= 2
        ans.raw.append(body)
        u = body.get("usage") or {}
        ans.tokens_in += int(u.get("prompt_tokens") or 0)
        ans.tokens_out += int(u.get("completion_tokens") or 0)
        ans.reasoning_tokens += int((u.get("completion_tokens_details") or {}).get(
            "reasoning_tokens") or 0)
        ch = body["choices"][0]
        return ch["message"].get("content") or "", ch.get("finish_reason") or ""

    def classify(self, image_path: str, user_text: str) -> Answer:
        """Classify one cached image. Never raises."""
        ans = Answer(ok=False)
        t0 = time.time()
        try:
            url = encode_image(image_path, int(self.vlm["image_side"]),
                               int(self.vlm.get("jpeg_quality", 90)))
            messages = [{"role": "system", "content": self.system},
                        {"role": "user", "content": [
                            {"type": "text", "text": user_text},
                            {"type": "image_url", "image_url": {"url": url}}]}]
            content, finish = self._post(messages, ans)
            reason = None
            for strict in (True, False):
                try:
                    if finish == "length":
                        raise ValueError("answer truncated (max_tokens reached)")
                    data = json.loads(strip_to_json(content))
                    ans.data, found = validate_result(data, self.tax, strict=strict)
                    ans.warnings += found           # keep the "repaired: ..." note
                    ans.ok = True
                    break
                except (ValueError, json.JSONDecodeError) as exc:
                    reason = str(exc)
                    if not strict:
                        break
                    messages = messages + [
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": REPAIR.format(reason=reason)}]
                    content, finish = self._post(messages, ans)
                    ans.warnings.append(f"repaired: {reason}")
            if not ans.ok:
                ans.error = f"invalid answer after repair: {reason}"
        except Exception as exc:
            ans.error = f"{type(exc).__name__}: {exc}"
        ans.latency_s = time.time() - t0
        return ans

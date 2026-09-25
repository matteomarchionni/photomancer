"""Semantic search over the catalog (photos and folders).

Each tagged photo is embedded from what the model said about it - genre,
subject, caption, subject tags, keywords, facets, dominant colours - with the
nomic-embed-text model already installed in LM Studio (same approach as the
BiblioMancer). A query is embedded the same way and compared by cosine
similarity, plus a small bonus when a query word is literally one of the
photo's tags/keywords (so "cat" ranks real cats first, never "scattered").

Only positive facts go into the text: "people: none" would make a photo
without people match the query "people" (embeddings do not understand "no").

nomic-embed needs task prefixes: "search_document: " for what is stored and
"search_query: " for what is asked.

Folder search aggregates photo scores per folder (see search_folders).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re

import numpy as np

from ._shared.embed import DOC_PREFIX, QUERY_PREFIX, folder_of, rank_folders, relevant  # noqa: F401
from ._shared.embed import Embedder as _SharedEmbedder
from .config import Settings
from .db import Catalog, now_iso

log = logging.getLogger("photomancer.search")

BATCH = 64
WORD_BONUS = 0.08          # added per query word found among the photo's own words (capped)

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_index(
    sha256 TEXT PRIMARY KEY, config_key TEXT, model TEXT, text_sha TEXT,
    words TEXT, vec BLOB, updated_at TEXT
);
"""


def _pretty(v: str) -> str:
    return str(v).replace("_", " ")


def photo_text(result: dict, color_names: list[str]) -> str:
    """Text a photo is embedded from (positive facts only)."""
    r = result
    parts = [f"{_pretty(r['primary_genre'])} photo, {_pretty(r['subgenre'])}"]
    if r.get("secondary_genres"):
        parts.append("also " + ", ".join(_pretty(g) for g in r["secondary_genres"]))
    parts.append(r.get("main_subject", ""))
    parts.append(r.get("caption", ""))
    tags = list(dict.fromkeys((r.get("subject_tags") or []) + (r.get("keywords") or [])))
    if tags:
        parts.append("tags: " + ", ".join(tags))
    p, a = r.get("people") or {}, r.get("animals") or {}
    if p.get("present"):
        parts.append(f"people: {p.get('count_bin')} person(s), {_pretty(p.get('role', ''))}")
    if a.get("present") and a.get("type") not in (None, "none"):
        parts.append(f"animal: {_pretty(a['type'])}, {_pretty(a.get('wild_or_domestic', ''))}")
    facts = [r.get("setting"), r.get("time_of_day"), r.get("weather_sky"), r.get("season")]
    facts = [_pretty(f) for f in facts if f and f not in ("unknown", "not_visible", "mixed")]
    techs = [_pretty(t) for t in r.get("techniques") or [] if t != "none"]
    if facts:
        parts.append(", ".join(facts))
    if techs:
        parts.append("technique: " + ", ".join(techs))
    if color_names:
        parts.append("colours: " + ", ".join(_pretty(c) for c in color_names))
    return ". ".join(x.strip().rstrip(".") for x in parts if x and x.strip()) + "."


_WORD = re.compile(r"[a-zà-ÿ0-9]+")


def _stem(w: str) -> str:
    """Tiny English plural folding: cats->cat, leaves->leave, glasses->glass."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words_of(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text.lower())}


def photo_words(result: dict) -> set[str]:
    """Words a query can match literally: genre, subgenre, tags, keywords, subject."""
    r = result
    src = [r["primary_genre"], r["subgenre"], r.get("main_subject", ""),
           " ".join(r.get("subject_tags") or []), " ".join(r.get("keywords") or [])]
    p, a = r.get("people") or {}, r.get("animals") or {}
    if p.get("present"):
        src.append("people person")
    if a.get("present") and a.get("type") not in (None, "none"):
        src.append(f"animal {a['type']}")
    return words_of(" ".join(_pretty(s) for s in src))


class Embedder(_SharedEmbedder):
    """LM Studio /v1/embeddings client (shared with BiblioMancer)."""

    def __init__(self, settings: Settings):
        super().__init__(settings.lm["base_url"],
                         settings.lm.get("embedding_model", "text-embedding-nomic-embed-text-v1.5"))


def update_index(settings: Settings, cat: Catalog, key: str) -> dict:
    """Embed every photo whose current result has no up-to-date vector. Incremental."""
    from .xmp_writer import effective_results
    cat.conn.executescript(INDEX_SCHEMA)
    emb = Embedder(settings)
    res = effective_results(cat, key, bool(settings.dedup.get("propagate")))
    have = {r["sha256"]: (r["text_sha"], r["model"]) for r in
            cat.q("SELECT sha256, text_sha, model FROM search_index")}
    min_frac = float(settings.xmp.get("min_color_fraction", 0.12))
    colors: dict[str, list[str]] = {}
    for r in cat.q("SELECT sha256, color_name, fraction FROM colors ORDER BY sha256, rank"):
        if r["fraction"] >= min_frac and r["color_name"] not in colors.setdefault(r["sha256"], []):
            colors[r["sha256"]].append(r["color_name"])
    todo = []
    for sha, (row, _rep) in res.items():
        result = json.loads(row["json"])
        text = photo_text(result, colors.get(sha, [])[:3])
        tsha = hashlib.sha1(text.encode("utf-8")).hexdigest()
        if have.get(sha) != (tsha, emb.model):
            todo.append((sha, tsha, text, " ".join(sorted(photo_words(result)))))
    log.info("search index: %d up to date, %d to embed", len(res) - len(todo), len(todo))
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        vecs = emb([t for _s, _h, t, _w in chunk], DOC_PREFIX)
        cat.x("INSERT OR REPLACE INTO search_index VALUES(?,?,?,?,?,?,?)",
              [(s, key, emb.model, h, w, v.tobytes(), now_iso())
               for (s, h, _t, w), v in zip(chunk, vecs)], many=True)
        log.info("embedded %d/%d", min(i + BATCH, len(todo)), len(todo))
    return {"indexed": len(res), "embedded": len(todo)}


class SearchIndex:
    """In-memory copy of the index for fast repeated queries (used by the panel)."""

    def __init__(self, settings: Settings, cat: Catalog, key: str):
        cat.conn.executescript(INDEX_SCHEMA)
        rows = cat.q("SELECT sha256, words, vec FROM search_index WHERE config_key=?", (key,))
        self.shas = [r["sha256"] for r in rows]
        self.words = [set((r["words"] or "").split()) for r in rows]
        self.vecs = (np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
                     if rows else np.zeros((0, 768), np.float32))
        self.pos = {s: i for i, s in enumerate(self.shas)}
        self.embed = Embedder(settings)

    def __len__(self) -> int:
        return len(self.shas)

    def query_vec(self, query: str) -> np.ndarray:
        return self.embed([query], QUERY_PREFIX)[0]

    def scores(self, query: str, q: np.ndarray | None = None) -> np.ndarray:
        """Hybrid score per indexed photo: cosine + literal-word bonus."""
        q = self.query_vec(query) if q is None else q
        sim = self.vecs @ q
        qw = words_of(query)
        if qw:
            hits = np.array([len(qw & w) / len(qw) for w in self.words], dtype=np.float32)
            sim = sim + WORD_BONUS * hits
        return sim


def search_folders(idx: SearchIndex, paths: dict[str, list[str]], query: str,
                   level: int = 0) -> list[dict]:
    """Rank folders for `query` (see _shared.embed.rank_folders).

    paths: sha256 -> relative paths of the files with that content.
    """
    q = idx.query_vec(query)
    return rank_folders(idx.shas, idx.vecs, idx.scores(query, q), q, paths, level)
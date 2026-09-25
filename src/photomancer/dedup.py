"""Near-duplicate groups (bursts, brackets, stacking frames) by perceptual hash.

pHash: 32x32 grey thumbnail -> 2-D DCT -> top-left 8x8 (DC excluded from the
median) -> 64 bits. Two photos are linked when their Hamming distance is at
most `hamming_threshold`; groups are the connected components.
"""

from __future__ import annotations

import logging

import numpy as np
from PIL import Image
from scipy.fft import dctn

from .db import Catalog

log = logging.getLogger("photomancer.dedup")


def phash(img: Image.Image) -> str:
    """64-bit perceptual hash of a PIL image, as 16 hex digits."""
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    d = dctn(g, norm="ortho")[:8, :8].flatten()
    med = np.median(d[1:])
    bits = (d > med).astype(np.uint8)
    return f"{int(''.join(map(str, bits)), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def group_duplicates(cat: Catalog, threshold: int) -> dict:
    """Recompute group_id for every hashed photo. Returns summary counts."""
    rows = cat.q("SELECT d.sha256, d.phash, MIN(f.path) AS path FROM dedup d "
                 "JOIN files f ON f.sha256 = d.sha256 AND f.missing = 0 "
                 "GROUP BY d.sha256 ORDER BY path")
    n = len(rows)
    if not n:
        return {"photos": 0, "groups": 0, "in_groups": 0}
    h = np.array([int(r["phash"], 16) for r in rows], dtype=np.uint64)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n - 1):
        dist = np.bitwise_count(h[i + 1:] ^ h[i])
        for j in np.nonzero(dist <= threshold)[0]:
            a, b = find(i), find(i + 1 + int(j))
            if a != b:
                parent[max(a, b)] = min(a, b)
    roots = [find(i) for i in range(n)]
    sizes: dict[int, int] = {}
    for r in roots:
        sizes[r] = sizes.get(r, 0) + 1
    # group_id = index of the representative (first path in sort order); singletons get NULL
    cat.x("UPDATE dedup SET group_id=? WHERE sha256=?",
          [(roots[i] if sizes[roots[i]] > 1 else None, rows[i]["sha256"]) for i in range(n)],
          many=True)
    groups = sum(1 for s in sizes.values() if s > 1)
    members = sum(s for s in sizes.values() if s > 1)
    log.info("near-duplicate groups: %d (%d photos in them, threshold %d bits)",
             groups, members, threshold)
    return {"photos": n, "groups": groups, "in_groups": members}


def representatives(cat: Catalog) -> dict[str, str]:
    """sha256 -> representative sha256 of its group (first path), for grouped photos."""
    rows = cat.q("SELECT d.sha256, d.group_id, MIN(f.path) AS path FROM dedup d "
                 "JOIN files f ON f.sha256 = d.sha256 AND f.missing = 0 "
                 "WHERE d.group_id IS NOT NULL GROUP BY d.sha256 ORDER BY path")
    first: dict[int, str] = {}
    out = {}
    for r in rows:
        rep = first.setdefault(r["group_id"], r["sha256"])
        out[r["sha256"]] = rep
    return out

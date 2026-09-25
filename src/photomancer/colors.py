"""Deterministic colour analysis (no VLM): k-means in CIELAB on the thumbnail.

Each of the k clusters gets its Lab centroid, sRGB hex, pixel fraction and
the nearest palette name by CIEDE2000. Global stats: mean and std of L*,
Hasler-Suesstrunk colourfulness, a monochrome flag and a tonality class.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache

# joblib/loky asks `wmic` for the physical core count and prints a traceback
# because wmic is gone from Windows 11: give it the answer up front.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))
try:
    import psutil
    from joblib.externals.loky.backend import context as _loky_ctx
    if getattr(_loky_ctx, "physical_cores_cache", None) is None:
        _loky_ctx.physical_cores_cache = psutil.cpu_count(logical=False) or os.cpu_count()
except Exception:
    pass

import numpy as np
from skimage.color import deltaE_ciede2000, lab2rgb, rgb2lab
from sklearn.cluster import KMeans


def hex_to_rgb01(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=float) / 255.0


@lru_cache(maxsize=8)
def _palette_lab(items: tuple) -> tuple[list[str], np.ndarray]:
    names = [n for n, _ in items]
    rgb = np.array([hex_to_rgb01(h) for _, h in items])
    return names, rgb2lab(rgb.reshape(1, -1, 3)).reshape(-1, 3)


def nearest_color(lab: np.ndarray, palette: dict[str, str]) -> str:
    """Palette name closest to one Lab colour (CIEDE2000)."""
    names, plab = _palette_lab(tuple(palette.items()))
    d = deltaE_ciede2000(np.repeat(lab.reshape(1, 3), len(names), axis=0), plab)
    return names[int(np.argmin(d))]


def colourfulness(rgb255: np.ndarray) -> float:
    """Hasler & Suesstrunk (2003) colourfulness metric on 0-255 sRGB pixels."""
    r, g, b = rgb255[:, 0], rgb255[:, 1], rgb255[:, 2]
    rg = r - g
    yb = 0.5 * (r + g) - b
    return float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2))


def analyse_colors(thumb, palette: dict[str, str], cfg: dict) -> tuple[list[dict], dict]:
    """Return (clusters sorted by fraction, stats) for a PIL RGB thumbnail."""
    rgb = np.asarray(thumb.convert("RGB"), dtype=np.float64).reshape(-1, 3)
    lab = rgb2lab((rgb / 255.0).reshape(1, -1, 3)).reshape(-1, 3)
    rng = np.random.default_rng(cfg.get("seed", 42))
    n = min(len(lab), int(cfg.get("sample_pixels", 20000)))
    sample = lab[rng.choice(len(lab), n, replace=False)] if n < len(lab) else lab
    k = int(cfg.get("k", 5))
    n_unique = len(np.unique(np.round(sample, 1), axis=0))
    k = max(1, min(k, n_unique))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")               # ConvergenceWarning on flat frames
        km = KMeans(n_clusters=k, n_init=3, random_state=cfg.get("seed", 42)).fit(sample)
    labels = km.predict(lab)
    counts = np.bincount(labels, minlength=k)
    clusters = []
    for idx in np.argsort(-counts):
        c = km.cluster_centers_[idx]
        rgb01 = np.clip(lab2rgb(c.reshape(1, 1, 3)).reshape(3), 0, 1)
        clusters.append({
            "lab_l": float(c[0]), "lab_a": float(c[1]), "lab_b": float(c[2]),
            "hex": "#" + "".join(f"{round(v * 255):02x}" for v in rgb01),
            "fraction": float(counts[idx] / len(lab)),
            "color_name": nearest_color(c, palette),
        })
    L = lab[:, 0]
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    mean_L = float(L.mean())
    clip = float(cfg.get("key_clip_share", 0.05))
    if mean_L < cfg.get("low_key_mean_L", 30) and (L > 70).mean() < clip:
        tonality = "low_key"
    elif mean_L > cfg.get("high_key_mean_L", 70) and (L < 30).mean() < clip:
        tonality = "high_key"
    else:
        tonality = "normal"
    stats = {
        "mean_L": mean_L, "std_L": float(L.std()), "colourfulness": colourfulness(rgb),
        "is_monochrome": int(float(np.percentile(chroma, 95)) < cfg.get("mono_chroma_p95", 10.0)),
        "tonality": tonality,
    }
    return clusters, stats

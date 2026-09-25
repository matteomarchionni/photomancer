"""EXIF extraction with Pillow (no exiftool needed) and derived prior features.

The features are priors, not labels: they may be shown to the model as
context and are used by the report to flag EXIF/VLM disagreements.
"""

from __future__ import annotations

from typing import Any

EXIF_IFD, GPS_IFD = 0x8769, 0x8825
T_MAKE, T_MODEL, T_ORIENT, T_DT = 271, 272, 274, 306
T_EXPO, T_FNUM, T_ISO, T_DTO = 33434, 33437, 34855, 36867
T_FOCAL, T_FOCAL35, T_LENS = 37386, 41989, 42036


def _f(v: Any) -> float | None:
    """IFDRational / tuple / number -> float (None when unusable)."""
    try:
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if v[1] else None
        x = float(v)
        return x if x == x else None          # NaN guard (0/0 rationals)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    s = str(v).replace("\x00", "").strip()
    return s or None


def _dms(v: Any, ref: Any) -> float | None:
    try:
        d, m, s = (_f(x) for x in v)
        if d is None:
            return None
        val = d + (m or 0) / 60 + (s or 0) / 3600
        return -val if _s(ref) in ("S", "W") else val
    except (TypeError, ValueError):
        return None


def extract_exif(img, cfg: dict) -> dict:
    """Read the EXIF of an opened PIL image into a flat dict (+ derived flags)."""
    try:
        ex = img.getexif()
    except Exception:
        ex = {}
    sub = gps = {}
    try:
        sub = ex.get_ifd(EXIF_IFD) or {}
        gps = ex.get_ifd(GPS_IFD) or {}
    except Exception:
        pass
    make, model = _s(ex.get(T_MAKE)), _s(ex.get(T_MODEL))
    camera = model or make
    if make and model and not model.upper().startswith(make.split()[0].upper()):
        camera = f"{make} {model}"
    focal = _f(sub.get(T_FOCAL))
    f35 = _f(sub.get(T_FOCAL35)) or None
    if not f35 and focal and model:
        crop = (cfg.get("crop_factors") or {}).get(model)
        f35 = round(focal * crop, 1) if crop else None
    shutter = _f(sub.get(T_EXPO))
    iso = sub.get(T_ISO)
    if isinstance(iso, tuple):
        iso = iso[0] if iso else None
    iso = int(iso) if isinstance(iso, (int, float)) and iso else None
    dto = _s(sub.get(T_DTO)) or _s(ex.get(T_DT))
    lat = lon = None
    if gps:
        lat, lon = _dms(gps.get(2), gps.get(1)), _dms(gps.get(4), gps.get(3))
        if lat is not None and lon is not None and lat == 0 and lon == 0:
            lat = lon = None                      # 0,0 = unset GPS block
    return {
        "camera": camera, "lens": _s(sub.get(T_LENS)), "focal_mm": focal, "focal_35mm": f35,
        "aperture": _f(sub.get(T_FNUM)), "shutter_s": shutter, "iso": iso,
        "datetime": dto, "lat": lat, "lon": lon,
        "is_long_exposure": int(bool(shutter and shutter > cfg.get("long_exposure_s", 1.0))),
        "is_high_iso": int(bool(iso and iso >= cfg.get("high_iso", 1600))),
        "is_telephoto": int(bool(f35 and f35 >= cfg.get("telephoto_35mm", 200))),
        "has_gps": int(lat is not None and lon is not None),
    }


def exif_summary(e: dict | None) -> str:
    """One-line EXIF context for the prompt ('' when nothing useful)."""
    if not e:
        return ""
    parts = []
    if e.get("camera"):
        parts.append(f"camera {e['camera']}")
    if e.get("focal_35mm"):
        parts.append(f"{e['focal_35mm']:.0f} mm (35mm-equiv.)")
    elif e.get("focal_mm"):
        parts.append(f"{e['focal_mm']:.0f} mm")
    if e.get("aperture"):
        parts.append(f"f/{e['aperture']:.1f}")
    s = e.get("shutter_s")
    if s:
        parts.append(f"{s:.1f} s" if s >= 1 else f"1/{round(1 / s)} s")
    if e.get("iso"):
        parts.append(f"ISO {e['iso']}")
    if e.get("datetime") and len(e["datetime"]) >= 13:
        parts.append(f"taken {e['datetime'][:7].replace(':', '-')} at {e['datetime'][11:13]}h local")
    if e.get("lat") is not None:
        parts.append("northern hemisphere" if e["lat"] >= 0 else "southern hemisphere")
    return ", ".join(parts)

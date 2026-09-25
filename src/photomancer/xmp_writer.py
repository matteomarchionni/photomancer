"""Stage 5 - export the catalog as XMP sidecars that Immich reads.

    photo.jpg -> photo.jpg.xmp   (Immich looks for <file>.xmp first, then
                                  <name>.xmp; the second form is already used
                                  here by Lightroom/ACR sidecars of RAW files)

Written fields:
    lr:HierarchicalSubject  AI|Genre|Landscape|Mountain, AI|Color|Blue, ...
                            (Immich turns these into nested tags AI/Genre/...)
    dc:subject              flat keywords (leaf values + free keywords)
    dc:description          the caption

Everything lives under the `AI` root, so it can be removed in one go. The
originals are never opened for writing. A sidecar that this tool did not
write, or that changed since it wrote it (e.g. edited from Immich), is left
alone and reported, unless --force.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from xml.sax.saxutils import escape

from .config import Settings
from .db import Catalog, now_iso
from .dedup import representatives
from .scan import long_path

log = logging.getLogger("photomancer.xmp")
GENERATOR = "photomancer"
NS = "https://github.com/matteomarchionni/photomancer/ns/1.0/"
# How a sidecar written by this tool is recognised - including the ones written
# before the rename to PhotoMancer (2026-09), so they stay "ours": refreshed,
# and removed by unwrite, never treated as another program's file.
OURS_MARKERS = (b'photomancer:Generator="photomancer"', b'photocat:Generator="photocat"')


def is_ours(content: bytes) -> bool:
    return any(m in content for m in OURS_MARKERS)


def pretty(v: str) -> str:
    """snake_case vocabulary value -> 'Sentence case' tag text."""
    s = str(v).replace("_", " ").strip()
    return (s[:1].upper() + s[1:]) if s else s


def _clean(v: str) -> str:
    """Tag-safe text: '|' and '/' would create extra hierarchy levels in Immich."""
    return " ".join(str(v).replace("|", " ").replace("/", " ").split())


def build_tags(result: dict, colors: list, stats, cfg: dict, low_conf: float) -> tuple[list, list]:
    """(hierarchical tags, flat keywords) for one photo."""
    root = cfg.get("root", "AI")
    h: list[str] = []
    flat: list[str] = []

    def add(*parts: str, leaf: bool = True) -> None:
        tag = "|".join([root] + [_clean(p) for p in parts])
        if tag not in h:
            h.append(tag)
        if leaf and _clean(parts[-1]).lower() not in (f.lower() for f in flat):
            flat.append(_clean(parts[-1]))

    add("Genre", pretty(result["primary_genre"]), pretty(result["subgenre"]))
    add("Genre", pretty(result["primary_genre"]))
    for g in result.get("secondary_genres") or []:
        add("Secondary genre", pretty(g))
    p = result.get("people") or {}
    if p.get("present"):
        add("People", p.get("count_bin", "1"), leaf=False)
        if p.get("role") not in (None, "none"):
            add("People role", pretty(p["role"]), leaf=False)
    else:
        add("People", "None", leaf=False)
    a = result.get("animals") or {}
    if a.get("present") and a.get("type") not in (None, "none"):
        add("Animals", pretty(a["type"]))
        if a.get("wild_or_domestic") not in (None, "none"):
            add("Animals", pretty(a["wild_or_domestic"]), leaf=False)
    for facet, label in (("setting", "Setting"), ("time_of_day", "Time"),
                         ("weather_sky", "Weather"), ("season", "Season")):
        v = result.get(facet)
        if v and v not in ("unknown", "not_visible"):
            add(label, pretty(v))
    for t in result.get("techniques") or []:
        if t != "none":
            add("Technique", pretty(t))
    if result.get("utility_flag") not in (None, "none"):
        add("Utility", pretty(result["utility_flag"]))
    names = []
    for c in colors:
        if len(names) >= int(cfg.get("max_colors", 3)):
            break
        if c["fraction"] >= float(cfg.get("min_color_fraction", 0.12)) and c["color_name"] not in names:
            names.append(c["color_name"])
    for n in names:
        add("Color", pretty(n))
    if stats is not None:
        if stats["is_monochrome"]:
            add("Tone", "Monochrome")
        if stats["tonality"] in ("low_key", "high_key"):
            add("Tone", pretty(stats["tonality"]))
    if float(result.get("genre_confidence") or 0) < low_conf:
        add("Review", "Low confidence", leaf=False)
    kws: list[str] = []
    for k in (result.get("keywords") or []) + (result.get("subject_tags") or []):
        k = _clean(k).lower()
        if k and k not in kws:
            kws.append(k)
    for k in kws:
        if cfg.get("keywords_as_tags", True):
            add("Keyword", k)
        elif k not in flat:
            flat.append(k)
    return h, flat


def render_xmp(hier: list[str], flat: list[str], caption: str, meta: dict) -> str:
    """XMP packet (UTF-8) with dc:subject, dc:description, lr:HierarchicalSubject."""
    li = lambda items: "\n".join(f"      <rdf:li>{escape(x)}</rdf:li>" for x in items)  # noqa: E731
    attrs = "".join(f'\n    photomancer:{k}="{escape(str(v), {chr(34): "&quot;"})}"'
                    for k, v in meta.items())
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="{GENERATOR}">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:lr="http://ns.adobe.com/lightroom/1.0/"
    xmlns:photomancer="{NS}"{attrs}>
   <dc:description>
    <rdf:Alt>
      <rdf:li xml:lang="x-default">{escape(caption)}</rdf:li>
    </rdf:Alt>
   </dc:description>
   <dc:subject>
    <rdf:Bag>
{li(flat)}
    </rdf:Bag>
   </dc:subject>
   <lr:HierarchicalSubject>
    <rdf:Bag>
{li(hier)}
    </rdf:Bag>
   </lr:HierarchicalSubject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
"""


def sidecar_path(full_jpeg: str) -> str:
    return full_jpeg + ".xmp"


def effective_results(cat: Catalog, key: str, propagate: bool) -> dict[str, tuple]:
    """sha256 -> (result row, propagated_from or None) for the active config."""
    rows = cat.q("SELECT v.* FROM vlm_results v JOIN (SELECT sha256, MAX(result_id) AS rid "
                 "FROM vlm_results WHERE config_key=? AND status='ok' GROUP BY sha256) m "
                 "ON m.rid = v.result_id", (key,))
    out = {r["sha256"]: (r, None) for r in rows}
    if propagate:
        for sha, rep in representatives(cat).items():
            if sha not in out and rep in out:
                out[sha] = (out[rep][0], rep)
    return out


def write_sidecars(settings: Settings, cat: Catalog, key: str, dry_run: bool = False,
                   force: bool = False, limit: int = 0) -> dict:
    """Write/refresh one sidecar per tagged JPEG. Returns counts."""
    res = effective_results(cat, key, bool(settings.dedup.get("propagate")))
    files = cat.q("SELECT file_id, path, sha256 FROM files WHERE missing=0 AND sha256 IS NOT NULL "
                  "ORDER BY path")
    prev = {r["file_id"]: r for r in cat.q("SELECT * FROM xmp_written")}
    counts = {"written": 0, "unchanged": 0, "foreign": 0, "edited": 0, "untagged": 0, "errors": 0}
    low = float(settings.report["low_confidence"])
    todo = [f for f in files if f["sha256"] in res]
    counts["untagged"] = len(files) - len(todo)
    if limit:
        todo = todo[:limit]
    log.info("=== write XMP sidecars ===")
    log.info("%d tagged photo(s) to export%s", len(todo), " (dry run: nothing is written)"
             if dry_run else "")
    for i, f in enumerate(todo, 1):
        row, rep = res[f["sha256"]]
        result = json.loads(row["json"])
        colors = cat.q("SELECT * FROM colors WHERE sha256=? ORDER BY rank", (f["sha256"],))
        stats = cat.one("SELECT * FROM color_stats WHERE sha256=?", (f["sha256"],))
        hier, flat = build_tags(result, colors, stats, settings.xmp, low)
        meta = {"Generator": GENERATOR, "ConfigKey": key, "ResultId": row["result_id"],
                "Confidence": f"{float(row['confidence'] or 0):.2f}"}
        if rep:
            meta["PropagatedFrom"] = rep[:16]
        text = render_xmp(hier, flat, result.get("caption", ""), meta)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        full = os.path.join(settings.photo_root, f["path"])
        side = sidecar_path(full)
        try:
            exists = os.path.exists(long_path(side))
            if exists:
                with open(long_path(side), "rb") as fh:
                    cur = fh.read()
                cur_sha = hashlib.sha256(cur).hexdigest()
                ours = is_ours(cur)
                p = prev.get(f["file_id"])
                if cur_sha == digest:
                    counts["unchanged"] += 1
                    continue
                if not ours and not force:
                    counts["foreign"] += 1
                    log.info("SKIP sidecar not written by this tool: %s", f["path"] + ".xmp")
                    continue
                if ours and p and p["content_sha"] != cur_sha and not force:
                    counts["edited"] += 1
                    log.info("SKIP sidecar edited since it was written: %s", f["path"] + ".xmp")
                    continue
            if dry_run:
                counts["written"] += 1
                if counts["written"] <= 5:
                    log.info("would write %s  (%d tags)", f["path"] + ".xmp", len(hier))
                continue
            with open(long_path(side) + ".tmp", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(long_path(side) + ".tmp", long_path(side))
            cat.x("INSERT OR REPLACE INTO xmp_written VALUES(?,?,?,?,?,?)",
                  (f["file_id"], f["sha256"], row["result_id"], side, digest, now_iso()))
            counts["written"] += 1
        except OSError as exc:
            counts["errors"] += 1
            log.info("ERROR writing %s: %s", side, exc)
        if i % 200 == 0:
            log.info("sidecars %d/%d", i, len(todo))
    log.info("sidecars: %(written)d written, %(unchanged)d unchanged, %(foreign)d foreign, "
             "%(edited)d edited elsewhere (kept), %(untagged)d not tagged yet, %(errors)d errors",
             counts)
    return counts


def remove_sidecars(settings: Settings, cat: Catalog, dry_run: bool = False) -> int:
    """Delete every sidecar this tool wrote (and only those). Returns the count."""
    n = 0
    for r in cat.q("SELECT file_id, sidecar FROM xmp_written"):
        side = r["sidecar"]
        try:
            with open(long_path(side), "rb") as fh:
                ours = is_ours(fh.read())
        except OSError:
            ours = False
        if ours:
            n += 1
            if not dry_run:
                os.remove(long_path(side))
        if not dry_run:
            cat.x("DELETE FROM xmp_written WHERE file_id=?", (r["file_id"],))
    log.info("%s %d sidecar(s)", "would remove" if dry_run else "removed", n)
    return n

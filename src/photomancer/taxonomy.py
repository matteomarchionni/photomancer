"""Controlled vocabulary, loaded from taxonomy.yaml (never hard-coded)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import yaml

FACET_KEYS = ("people_count_bin", "people_role", "animal_type", "wild_or_domestic", "setting",
              "time_of_day", "weather_sky", "season", "techniques", "utility_flag")


@dataclass
class Taxonomy:
    """Genres, subgenres, decision rules, facets and colour palette."""

    version: str
    sha8: str
    genres: dict[str, dict]            # name -> {"description", "subgenres": [...]}
    decision_rules: list[str]
    facets: dict[str, list[str]]
    palette: dict[str, str]            # colour name -> #rrggbb

    @property
    def genre_names(self) -> list[str]:
        return list(self.genres)

    @property
    def all_subgenres(self) -> list[str]:
        seen: list[str] = []
        for g in self.genres.values():
            seen += [s for s in g["subgenres"] if s not in seen]
        return seen

    def subgenres_of(self, genre: str) -> list[str]:
        return list(self.genres.get(genre, {}).get("subgenres", []))

    def genre_of_subgenre(self, sub: str) -> list[str]:
        return [g for g, v in self.genres.items() if sub in v["subgenres"]]


def load_taxonomy(path: str) -> Taxonomy:
    """Parse and sanity-check taxonomy.yaml; raises ValueError on a broken file."""
    with open(path, "rb") as fh:
        blob = fh.read()
    data = yaml.safe_load(blob.decode("utf-8"))
    genres = data.get("genres") or {}
    if not genres:
        raise ValueError(f"{path}: no genres")
    for name, g in genres.items():
        if not g.get("subgenres"):
            raise ValueError(f"{path}: genre {name} has no subgenres")
        g["subgenres"] = [str(s) for s in g["subgenres"]]
        g.setdefault("description", "")
    facets = {k: [str(v) for v in (data.get("facets") or {}).get(k, [])] for k in FACET_KEYS}
    missing = [k for k, v in facets.items() if not v]
    if missing:
        raise ValueError(f"{path}: facets missing: {', '.join(missing)}")
    return Taxonomy(
        version=str(data.get("taxonomy_version", "0")),
        sha8=hashlib.sha256(blob).hexdigest()[:8],
        genres=genres,
        decision_rules=[str(r) for r in data.get("decision_rules") or []],
        facets=facets,
        palette={str(k): str(v) for k, v in (data.get("color_palette") or {}).items()},
    )

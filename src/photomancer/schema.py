"""Output schema: JSON Schema for LM Studio's structured output, and Pydantic
validation against the taxonomy (both generated from taxonomy.yaml).

Hard errors (-> repair retry, then `failed`): unknown enum values, subgenre
not belonging to primary_genre, malformed structure; caption over 25 words
on the first attempt. Soft fixes (recorded as warnings on the result):
lower-casing and trimming lists, dropping primary from secondary genres,
people/animal consistency; after the repair attempt also caption truncation
and moving primary_genre to the only genre that owns the chosen subgenre.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .taxonomy import Taxonomy

MAX_CAPTION_WORDS = 25
MAX_KEYWORDS = 10
MAX_SUBJECT_TAGS = 8
MAX_SECONDARY = 2
MAX_TECHNIQUES = 4


def json_schema(tax: Taxonomy) -> dict:
    """Schema passed as response_format; the server constrains decoding to it."""
    f = tax.facets
    enum = lambda vals: {"type": "string", "enum": list(vals)}  # noqa: E731
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "primary_genre": enum(tax.genre_names),
            "subgenre": enum(tax.all_subgenres),
            "secondary_genres": {"type": "array", "items": enum(tax.genre_names),
                                 "maxItems": MAX_SECONDARY},
            "genre_confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "main_subject": {"type": "string"},
            "subject_tags": {"type": "array", "items": {"type": "string"},
                             "maxItems": MAX_SUBJECT_TAGS},
            "people": {"type": "object", "additionalProperties": False,
                       "properties": {"present": {"type": "boolean"},
                                      "count_bin": enum(f["people_count_bin"]),
                                      "role": enum(f["people_role"])},
                       "required": ["present", "count_bin", "role"]},
            "animals": {"type": "object", "additionalProperties": False,
                        "properties": {"present": {"type": "boolean"},
                                       "type": enum(f["animal_type"]),
                                       "wild_or_domestic": enum(f["wild_or_domestic"])},
                        "required": ["present", "type", "wild_or_domestic"]},
            "setting": enum(f["setting"]),
            "time_of_day": enum(f["time_of_day"]),
            "weather_sky": enum(f["weather_sky"]),
            "season": enum(f["season"]),
            "techniques": {"type": "array", "items": enum(f["techniques"]),
                           "maxItems": MAX_TECHNIQUES},
            "utility_flag": enum(f["utility_flag"]),
            "caption": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_KEYWORDS},
        },
        "required": ["primary_genre", "subgenre", "secondary_genres", "genre_confidence",
                     "main_subject", "subject_tags", "people", "animals", "setting",
                     "time_of_day", "weather_sky", "season", "techniques", "utility_flag",
                     "caption", "keywords"],
    }


def _lit(values: list[str]) -> Any:
    return Literal[tuple(values)]  # type: ignore[valid-type]


_MODELS: dict[str, type[BaseModel]] = {}


def result_model(tax: Taxonomy) -> type[BaseModel]:
    """Pydantic model with the taxonomy's enums (cached per taxonomy hash)."""
    if tax.sha8 in _MODELS:
        return _MODELS[tax.sha8]
    f = tax.facets
    strict = ConfigDict(extra="forbid")
    People = create_model("People", __config__=strict, present=(bool, ...),
                          count_bin=(_lit(f["people_count_bin"]), ...),
                          role=(_lit(f["people_role"]), ...))
    Animals = create_model("Animals", __config__=strict, present=(bool, ...),
                           type=(_lit(f["animal_type"]), ...),
                           wild_or_domestic=(_lit(f["wild_or_domestic"]), ...))
    G = _lit(tax.genre_names)
    model = create_model(
        "VlmResult", __config__=strict,
        primary_genre=(G, ...),
        subgenre=(_lit(tax.all_subgenres), ...),
        secondary_genres=(list[G], Field(default_factory=list, max_length=MAX_SECONDARY)),
        genre_confidence=(float, Field(ge=0.0, le=1.0)),
        main_subject=(str, Field(min_length=1)),
        subject_tags=(list[str], Field(default_factory=list, max_length=MAX_SUBJECT_TAGS)),
        people=(People, ...), animals=(Animals, ...),
        setting=(_lit(f["setting"]), ...), time_of_day=(_lit(f["time_of_day"]), ...),
        weather_sky=(_lit(f["weather_sky"]), ...), season=(_lit(f["season"]), ...),
        techniques=(list[_lit(f["techniques"])], Field(default_factory=list)),
        utility_flag=(_lit(f["utility_flag"]), ...),
        caption=(str, Field(min_length=1)),
        keywords=(list[str], Field(default_factory=list, max_length=MAX_KEYWORDS)),
    )
    _MODELS[tax.sha8] = model
    return model


def _clean_list(items: Any, limit: int, name: str, warns: list[str],
                free_text: bool = False) -> list[str]:
    out: list[str] = []
    for x in items if isinstance(items, list) else []:
        s = str(x).strip().lower()
        if free_text:                       # 'night_sky' -> 'night sky'
            s = s.replace("_", " ")
        s = " ".join(s.split())
        if s and s not in out:
            out.append(s)
    if len(out) > limit:
        warns.append(f"{name}: kept first {limit} of {len(out)}")
        out = out[:limit]
    return out


def validate_result(data: Any, tax: Taxonomy, strict: bool = True) -> tuple[dict, list[str]]:
    """Normalise + validate one model answer. Raises ValueError with a readable reason."""
    if not isinstance(data, dict):
        raise ValueError("answer is not a JSON object")
    d = dict(data)
    warns: list[str] = []
    d["keywords"] = _clean_list(d.get("keywords"), MAX_KEYWORDS, "keywords", warns, True)
    d["subject_tags"] = _clean_list(d.get("subject_tags"), MAX_SUBJECT_TAGS, "subject_tags", warns,
                                    True)
    sec = [g for g in _clean_list(d.get("secondary_genres"), 99, "secondary", warns)
           if g != d.get("primary_genre")]
    if len(sec) != len(d.get("secondary_genres") or []):
        warns.append("secondary_genres: removed primary genre / duplicates")
    d["secondary_genres"] = sec[:MAX_SECONDARY]
    tech = _clean_list(d.get("techniques"), 99, "techniques", warns)
    if "none" in tech and len(tech) > 1:
        tech.remove("none")
    d["techniques"] = tech[:MAX_TECHNIQUES]
    try:
        d["genre_confidence"] = min(1.0, max(0.0, float(d.get("genre_confidence"))))
    except (TypeError, ValueError):
        raise ValueError("genre_confidence is not a number")
    p = d.get("people")
    if isinstance(p, dict):
        p = dict(p)
        if p.get("present") is False and (p.get("count_bin") != "0" or p.get("role") != "none"):
            p["count_bin"], p["role"] = "0", "none"
            warns.append("people: absent -> count 0, role none")
        elif p.get("present") is True and p.get("count_bin") == "0":
            p["count_bin"] = "1"
            warns.append("people: present with count 0 -> 1")
        d["people"] = p
    a = d.get("animals")
    if isinstance(a, dict) and a.get("present") is False and (
            a.get("type") != "none" or a.get("wild_or_domestic") != "none"):
        d["animals"] = dict(a, type="none", wild_or_domestic="none")
        warns.append("animals: absent -> none")
    cap = " ".join(str(d.get("caption", "")).split())
    words = cap.split()
    if len(words) > MAX_CAPTION_WORDS:
        if strict:
            raise ValueError(f"caption has {len(words)} words (max {MAX_CAPTION_WORDS})")
        cap = " ".join(words[:MAX_CAPTION_WORDS]).rstrip(",;:") + "…"
        warns.append("caption truncated to 25 words")
    d["caption"] = cap
    d["main_subject"] = " ".join(str(d.get("main_subject", "")).split())
    try:
        obj = result_model(tax).model_validate(d)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5])
        raise ValueError(f"schema: {errs}") from None
    out = obj.model_dump()
    if out["subgenre"] not in tax.subgenres_of(out["primary_genre"]):
        owners = tax.genre_of_subgenre(out["subgenre"])
        if not strict and len(owners) == 1:
            # after a failed repair: the subgenre is the more specific choice, so the
            # genre follows it (measured case: other/object_closeup -> macro_closeup)
            warns.append(f"primary_genre {out['primary_genre']} -> {owners[0]} "
                         f"(to match subgenre {out['subgenre']})")
            out["primary_genre"] = owners[0]
            out["secondary_genres"] = [g for g in out["secondary_genres"] if g != owners[0]]
        else:
            raise ValueError(f"subgenre '{out['subgenre']}' does not belong to "
                             f"'{out['primary_genre']}' (it belongs to: "
                             f"{', '.join(owners) or 'none'})")
    return out, warns

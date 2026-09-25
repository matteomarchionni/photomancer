"""Prompt builder: embeds the controlled vocabulary from taxonomy.yaml.

The built-in prompt is versioned (PROMPT_VERSION): bump it on any change to
the wording below. A custom prompt file gets version 'custom-<hash>', so
results from different prompts are never mixed.
"""

from __future__ import annotations

import hashlib
import os

from .taxonomy import Taxonomy

PROMPT_VERSION = "1.0"

INSTRUCTIONS = """You are cataloguing a personal photography archive. Look at the photo and \
return ONE JSON object that describes it, using ONLY the controlled vocabulary below.

{taxonomy}

FIELDS
- primary_genre: exactly one genre (the genre of the MAIN SUBJECT).
- subgenre: exactly one subgenre FROM THE LIST OF THAT primary_genre.
- secondary_genres: 0-2 other genres that clearly also apply; never the primary genre.
- genre_confidence: 0.0-1.0, how sure you are of primary_genre. Be honest.
- main_subject: short factual noun phrase (max 12 words).
- subject_tags: up to 8 lowercase English nouns for clearly visible things.
- people: present (true/false), count_bin, role.
- animals: present (true/false), type, wild_or_domestic.
- setting, time_of_day, weather_sky, season: one value each from the lists; use \
unknown / not_visible when the photo does not show it.
- techniques: photographic techniques clearly visible (0-4 values); ["none"] if none.
- utility_flag: technical problem with the file, "none" if it is fine.
- caption: ONE factual English sentence, at most 25 words.
- keywords: up to 10 lowercase English search keywords.

STRICT RULES
- Describe only what is visible. No aesthetic judgement ("stunning", "beautiful").
- Never name or guess the identity of people.
- Never name a specific place, landmark, mountain, city or species unless visible text or a \
world-famous landmark makes it unambiguous; otherwise stay generic ("alpine lake", "gull").
- Answer with the JSON object only, compactly on ONE line (no indentation, no line breaks)."""


def taxonomy_block(tax: Taxonomy) -> str:
    """Vocabulary section: genres with subgenres, decision rules, facets."""
    lines = ["GENRES (listed with the owner's main areas first: consider those first)"]
    for name, g in tax.genres.items():
        lines.append(f"- {name}: {g['description']}")
        lines.append(f"    subgenres: {', '.join(g['subgenres'])}")
    if tax.decision_rules:
        lines.append("")
        lines.append("DECISION RULES")
        lines += [f"- {r}" for r in tax.decision_rules]
    f = tax.facets
    lines += ["", "FACET VALUES",
              f"- people.count_bin: {', '.join(f['people_count_bin'])}",
              f"- people.role: {', '.join(f['people_role'])}",
              f"- animals.type: {', '.join(f['animal_type'])}",
              f"- animals.wild_or_domestic: {', '.join(f['wild_or_domestic'])}",
              f"- setting: {', '.join(f['setting'])}",
              f"- time_of_day: {', '.join(f['time_of_day'])}",
              f"- weather_sky: {', '.join(f['weather_sky'])}",
              f"- season: {', '.join(f['season'])}",
              f"- techniques: {', '.join(f['techniques'])}",
              f"- utility_flag: {', '.join(f['utility_flag'])}"]
    return "\n".join(lines)


def build_system_prompt(tax: Taxonomy, prompt_file: str = "") -> tuple[str, str]:
    """Return (system prompt, prompt_version)."""
    template, version = INSTRUCTIONS, PROMPT_VERSION
    if prompt_file:
        if not os.path.exists(prompt_file):
            raise FileNotFoundError(f"prompt file not found: {prompt_file}")
        with open(prompt_file, encoding="utf-8") as fh:
            template = fh.read()
        version = "custom-" + hashlib.sha256(template.encode("utf-8")).hexdigest()[:8]
        if "{taxonomy}" not in template:
            template += "\n\n{taxonomy}"
    return template.replace("{taxonomy}", taxonomy_block(tax)), version


def user_text(exif_line: str) -> str:
    """Text part of the user turn (the image travels next to it)."""
    t = "Catalogue this photo."
    if exif_line:
        t += f"\nCamera data (a hint only - the image decides): {exif_line}."
    return t


REPAIR = ("Your previous answer was rejected: {reason}. Answer again with a corrected JSON "
          "object. Remember: subgenre must be one of the subgenres listed under the "
          "primary_genre you choose; caption at most 25 words.")

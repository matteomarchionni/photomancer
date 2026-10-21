"""Unit tests: schema validation, colour mapping, resume logic, XMP, config edits.

Run from the repository folder:  python -m pytest tests -q
No model and no real photos needed (synthetic images in a temp folder).
"""

from __future__ import annotations

import copy
import json
import os
import sys

import numpy as np
import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

from photomancer.colors import analyse_colors, nearest_color  # noqa: E402
from photomancer.config import DEFAULTS, Settings, update_config  # noqa: E402
from photomancer.db import Catalog  # noqa: E402
from photomancer.dedup import group_duplicates, hamming, phash  # noqa: E402
from photomancer.preprocess import pending_prepare, run_prepare  # noqa: E402
from photomancer.scan import scan  # noqa: E402
from photomancer.schema import json_schema, validate_result  # noqa: E402
from photomancer.taxonomy import load_taxonomy  # noqa: E402
from photomancer.xmp_writer import build_tags, is_ours, render_xmp  # noqa: E402

TAX = load_taxonomy(os.path.join(os.path.dirname(HERE), "src", "photomancer", "assets", "taxonomy.yaml"))

GOOD = {
    "primary_genre": "landscape", "subgenre": "mountain", "secondary_genres": ["travel"],
    "genre_confidence": 0.82, "main_subject": "snow-capped alpine ridge",
    "subject_tags": ["Mountain", "snow"], "people": {"present": False, "count_bin": "0", "role": "none"},
    "animals": {"present": False, "type": "none", "wild_or_domestic": "none"},
    "setting": "outdoor", "time_of_day": "golden_hour", "weather_sky": "partly_cloudy",
    "season": "winter", "techniques": ["panorama"], "utility_flag": "none",
    "caption": "Snow-covered alpine ridge lit by late-afternoon light above a shaded valley.",
    "keywords": ["Alps", "ridge", "snow", "golden hour", "valley"],
}


# ------------------------------------------------------------- schema --
def test_valid_result_is_normalised():
    out, warns = validate_result(copy.deepcopy(GOOD), TAX)
    assert out["keywords"][0] == "alps" and out["subject_tags"][0] == "mountain"
    assert out["primary_genre"] == "landscape" and not warns


def test_subgenre_must_belong_to_genre():
    bad = dict(GOOD, subgenre="birds")
    with pytest.raises(ValueError, match="does not belong"):
        validate_result(bad, TAX)


def test_unknown_enum_rejected():
    with pytest.raises(ValueError, match="schema"):
        validate_result(dict(GOOD, time_of_day="noon"), TAX)


def test_secondary_never_primary_and_capped():
    out, warns = validate_result(dict(GOOD, secondary_genres=["landscape", "travel", "street",
                                                              "portrait"]), TAX)
    assert "landscape" not in out["secondary_genres"] and len(out["secondary_genres"]) <= 2
    assert warns


def test_caption_length_strict_then_lenient():
    long = dict(GOOD, caption=" ".join(["word"] * 30))
    with pytest.raises(ValueError, match="caption"):
        validate_result(long, TAX, strict=True)
    out, warns = validate_result(long, TAX, strict=False)
    assert len(out["caption"].split()) == 25 and warns


def test_people_consistency_fixed():
    out, warns = validate_result(dict(GOOD, people={"present": False, "count_bin": "2-5",
                                                    "role": "crowd"}), TAX)
    assert out["people"] == {"present": False, "count_bin": "0", "role": "none"} and warns


def test_json_schema_uses_taxonomy():
    s = json_schema(TAX)
    assert s["properties"]["primary_genre"]["enum"] == TAX.genre_names
    assert "milky_way_nightscape" in s["properties"]["subgenre"]["enum"]


# ------------------------------------------------------------- colours --
def test_nearest_color_names():
    from skimage.color import rgb2lab
    lab = lambda rgb: rgb2lab(np.array(rgb, float).reshape(1, 1, 3) / 255).reshape(3)  # noqa: E731
    assert nearest_color(lab((0, 0, 0)), TAX.palette) == "black"
    assert nearest_color(lab((255, 255, 255)), TAX.palette) == "white"
    assert nearest_color(lab((31, 95, 168)), TAX.palette) == "blue"


def test_analyse_colors_split_and_monochrome():
    img = Image.new("RGB", (100, 100), (31, 95, 168))
    img.paste((255, 255, 255), (0, 0, 100, 30))
    clusters, stats = analyse_colors(img, TAX.palette, DEFAULTS["colors"])
    assert clusters[0]["color_name"] == "blue" and abs(clusters[0]["fraction"] - 0.7) < 0.02
    assert not stats["is_monochrome"]
    grey = Image.new("RGB", (64, 64), (20, 20, 20))
    _c, st = analyse_colors(grey, TAX.palette, DEFAULTS["colors"])
    assert st["is_monochrome"] and st["tonality"] == "low_key"


def test_phash_similar_images_close():
    rng = np.random.default_rng(1)
    a = Image.fromarray((rng.random((64, 64, 3)) * 255).astype("uint8"))
    b = a.copy()
    b.paste((255, 255, 255), (0, 0, 2, 2))
    c = Image.fromarray((rng.random((64, 64, 3)) * 255).astype("uint8"))
    assert hamming(phash(a), phash(b)) <= 6 < hamming(phash(a), phash(c))


# ------------------------------------------------------ resume / scan --
def _settings(tmp_path) -> Settings:
    raw = copy.deepcopy(DEFAULTS)
    raw["photo_root"] = str(tmp_path / "photos")
    raw["data_dir"] = str(tmp_path / "data")
    raw["taxonomy_file"] = os.path.join(os.path.dirname(HERE), "src", "photomancer", "assets", "taxonomy.yaml")
    raw["preprocess"]["workers"] = 2
    s = Settings(raw)
    s.photo_root, s.data_dir = raw["photo_root"], raw["data_dir"]
    s.derive_paths()
    s.ensure_dirs()
    return s


def test_scan_prepare_resume(tmp_path):
    s = _settings(tmp_path)
    os.makedirs(os.path.join(s.photo_root, "album"), exist_ok=True)
    for i, col in enumerate([(200, 30, 30), (30, 200, 30), (30, 30, 200)]):
        Image.new("RGB", (300, 200), col).save(os.path.join(s.photo_root, "album", f"p{i}.JPG"))
    Image.new("RGB", (300, 200), (200, 30, 30)).save(os.path.join(s.photo_root, "dup.jpeg"))
    cat = Catalog(s.db_path)
    assert scan(s, cat)["new"] == 4
    assert len(pending_prepare(cat)) == 4
    run_prepare(s, cat, pending_prepare(cat)[:2], TAX.palette)      # "crash" after two
    assert len(pending_prepare(cat)) == 2                               # resumes with the rest
    run_prepare(s, cat, pending_prepare(cat), TAX.palette)
    assert pending_prepare(cat) == []
    assert scan(s, cat)["new"] == 0                                      # idempotent
    shas = [r[0] for r in cat.q("SELECT sha256 FROM files")]
    assert len(set(shas)) == 3                                           # exact duplicate shares a hash
    # a changed file is re-queued
    Image.new("RGB", (300, 200), (9, 9, 9)).save(os.path.join(s.photo_root, "album", "p1.JPG"))
    os.utime(os.path.join(s.photo_root, "album", "p1.JPG"), (1e9, 1e9))
    assert scan(s, cat)["changed"] == 1 and len(pending_prepare(cat)) == 1
    assert group_duplicates(cat, 6)["photos"] == 2                        # p1 awaits re-preparation


# ----------------------------------------------------------------- XMP --
def test_xmp_tags_and_render():
    out, _ = validate_result(copy.deepcopy(GOOD), TAX)
    colors = [{"color_name": "sky_blue", "fraction": 0.5}, {"color_name": "white", "fraction": 0.3}]
    h, flat = build_tags(out, colors, {"is_monochrome": 0, "tonality": "normal"},
                         DEFAULTS["xmp"], 0.5)
    assert "AI|Genre|Landscape|Mountain" in h and "AI|Color|Sky blue" in h
    assert "AI|People|None" in h and "AI|Keyword|golden hour" in h
    x = render_xmp(h, flat, out["caption"], {"Generator": "photomancer"})
    assert "<lr:HierarchicalSubject>" in x and 'photomancer:Generator="photomancer"' in x
    import xml.dom.minidom
    xml.dom.minidom.parseString(x.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])   # well-formed


# -------------------------------------------------------- config edits --
def test_update_config_keeps_comments(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("photo_root: 'a'\nvlm:\n  image_side: 1024          # long side\n  concurrency: 2\n"
                 "lmstudio:\n  model: x\n", encoding="utf-8")
    update_config({"vlm.concurrency": 3, "lmstudio.model": "qwen/q", "photo_root": r"C:\it's"},
                  str(p))
    t = p.read_text(encoding="utf-8")
    assert "  concurrency: 3" in t and "# long side" in t and "model: 'qwen/q'" in t
    import yaml
    assert yaml.safe_load(t)["photo_root"] == r"C:\it's"
    assert json.dumps(yaml.safe_load(t))


# -------------------------------------------------------------- search --
def test_search_words_are_whole_words_not_substrings():
    from photomancer.search import photo_words, words_of
    out, _ = validate_result(copy.deepcopy(GOOD), TAX)
    w = photo_words(out)
    assert "mountain" in w and "ridge" in w
    assert not words_of("cat") <= words_of("scattered clouds over a vacation spot")
    assert words_of("cats") <= words_of("two cat on a roof")          # plural folding


def test_photo_text_has_no_negative_facts():
    from photomancer.search import photo_text
    out, _ = validate_result(copy.deepcopy(GOOD), TAX)
    t = photo_text(out, ["sky_blue"])
    assert "people" not in t.lower() and "animal" not in t.lower()    # absent = not mentioned
    assert "landscape photo, mountain" in t and "sky blue" in t


def test_lenient_pass_moves_genre_to_owner_of_subgenre():
    bad = dict(GOOD, primary_genre="other", subgenre="object_closeup", secondary_genres=[])
    with pytest.raises(ValueError, match="does not belong"):
        validate_result(copy.deepcopy(bad), TAX, strict=True)
    out, warns = validate_result(copy.deepcopy(bad), TAX, strict=False)
    assert out["primary_genre"] == "macro_closeup" and any("->" in w for w in warns)


# ---------------------------------------------------------------- backup --
def test_backup_snapshot_and_previous(tmp_path):
    from photomancer.backup import backup
    s = _settings(tmp_path)
    assert backup(s) is False                       # no backup_dir = off
    s.backup_dir = str(tmp_path / "bk")
    cat = Catalog(s.db_path)
    cat.conn.execute("INSERT OR REPLACE INTO meta VALUES('probe', 'one')")
    cat.conn.commit()
    assert backup(s)                                # works with the catalog still open (WAL)
    cat.conn.execute("INSERT OR REPLACE INTO meta VALUES('probe', 'two')")
    cat.conn.commit()
    cat.close()
    assert backup(s)
    import sqlite3
    val = lambda p: sqlite3.connect(p).execute("SELECT value FROM meta WHERE key='probe'").fetchone()[0]
    assert val(os.path.join(s.backup_dir, "catalog.db")) == "two"
    assert val(os.path.join(s.backup_dir, "catalog.prev.db")) == "one"
    assert os.path.exists(os.path.join(s.backup_dir, "code", "src", "photomancer", "backup.py"))
    assert os.path.exists(os.path.join(s.backup_dir, "backup_manifest.json"))

def test_backup_refuses_unexcluded_folder_in_archive(tmp_path):
    from photomancer.backup import backup
    s = _settings(tmp_path)
    Catalog(s.db_path).close()
    s.backup_dir = os.path.join(s.photo_root, "bk")          # scanned -> refused
    assert backup(s) is False and not os.path.exists(s.backup_dir)
    s.backup_dir = os.path.join(s.photo_root, "photomancer_backup")   # excluded -> fine
    assert backup(s)


def test_sidecar_ownership_old_and_new_marker():
    """Sidecars from before the rename (photocat) are still ours; foreign ones are not."""
    new = render_xmp(["AI|Genre|Landscape"], ["Landscape"], "A lake.", {"Generator": "photomancer"})
    assert is_ours(new.encode("utf-8"))
    assert is_ours(b'<rdf:Description photocat:Generator="photocat" />')
    assert not is_ours(b'<rdf:Description xmp:CreatorTool="digiKam" />')


def test_photo_year_from_exif_only():
    from photomancer.report import photo_year
    assert photo_year({"datetime": "2019:07:14 18:02:11"}) == "2019"
    assert photo_year({"datetime": "2021-03-05T09:00:00"}) == "2021"
    assert photo_year({"datetime": "0000:00:00 00:00:00"}) == ""        # blank camera clock
    assert photo_year({"datetime": None}) == "" and photo_year({}) == "" and photo_year(None) == ""


def test_year_filter_choices_and_matching():
    from photomancer.report import ALL_YEARS, NO_DATE, year_choices, year_matches
    assert year_choices(["2019", "", "2021", "2019"]) == [ALL_YEARS, "2021", "2019", NO_DATE]
    assert year_choices(["2020"]) == [ALL_YEARS, "2020"]                  # no 'No date' if none missing
    assert year_matches("2019", ALL_YEARS) and year_matches("", ALL_YEARS)
    assert year_matches("2019", "2019") and not year_matches("2020", "2019")
    assert year_matches("", NO_DATE) and not year_matches("2019", NO_DATE)


def test_catalog_rows_carry_the_exif_year(tmp_path):
    from photomancer.report import catalog_rows
    s = _settings(tmp_path)
    cat = Catalog(s.db_path)
    cat.conn.executemany("INSERT INTO files(path, sha256, missing) VALUES(?,?,0)",
                         [("a.jpg", "sa"), ("b.jpg", "sb")])
    cat.conn.execute("INSERT INTO exif(sha256, datetime) VALUES('sa', '2018:05:01 10:00:00')")
    cat.conn.commit()
    years = {r["path"]: r["year"] for r in catalog_rows(s, cat, "k")}
    cat.close()
    assert years == {"a.jpg": "2018", "b.jpg": ""}

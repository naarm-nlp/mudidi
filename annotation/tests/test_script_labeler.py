"""Unit tests for the unified script labeler."""

from __future__ import annotations

from script_check import assign_char_script_labels, script_label_for_bucket  # noqa: E402
from script_labeler import label_page  # noqa: E402

from mudidi.schemas.language_span import SPACE  # noqa: E402


def _script_at(page_map, raw, needle):
    start = raw.index(needle)
    char_map = page_map.language_char_map(raw)
    scripts = {char_map[i] for i in range(start, start + len(needle)) if not raw[i].isspace()}
    assert len(scripts) == 1, f"{needle!r} spans multiple scripts: {scripts}"
    return next(iter(scripts))


def test_ipa_and_latin_partition_on_canala_page():
    raw = "akɔɔtee small crab\n"
    page_map = label_page(raw, dictionary="Canala-English", page=1)
    page_map.validate_against(raw)
    assert _script_at(page_map, raw, "akɔɔtee") == "IPA"
    assert _script_at(page_map, raw, "small crab") == "Latin"


def test_greek_and_latin_partition():
    raw = "<b>᾿Αδράχνω</b>, v. a. to seize.\n"
    page_map = label_page(raw, dictionary="Greek-English", page=38)
    page_map.validate_against(raw)
    assert _script_at(page_map, raw, "Αδράχνω") == "Greek"
    assert _script_at(page_map, raw, "seize") == "Latin"


def test_cyrillic_and_extended_cyrillic():
    raw = "ԓӄӈ русский\n"
    page_map = label_page(raw, dictionary="Chukchi-Russian", page=1)
    page_map.validate_against(raw)
    assert _script_at(page_map, raw, "ԓӄӈ") == "Cyrillic Extended"
    assert _script_at(page_map, raw, "русский") == "Cyrillic"


def test_mixed_scripts_on_assyrian_page():
    raw = "turi Heb. צור\n"
    page_map = label_page(raw, dictionary="Assyrian-English", page=1)
    page_map.validate_against(raw)
    assert _script_at(page_map, raw, "turi") == "Latin"
    assert _script_at(page_map, raw, "צור") == "Hebrew"


def test_whitespace_is_space():
    raw = "akɔɔtee small\n"
    page_map = label_page(raw, dictionary="Canala-English", page=1)
    char_map = page_map.language_char_map(raw)
    assert char_map[raw.index(" ")] == SPACE
    assert char_map[raw.index("\n")] == SPACE


def test_punctuation_inherits_surrounding_script():
    raw = "akɔɔtee, small\n"
    labels = assign_char_script_labels(raw, space_label=SPACE)
    comma_index = raw.index(",")
    assert labels[comma_index] == "IPA"


def test_latin_extended_for_inupiatun_headwords():
    raw = "<b>suġaiḷaq</b> ice\n"
    page_map = label_page(raw, dictionary="Iñupiatun Eskimo-English", page=1)
    page_map.validate_against(raw)
    assert _script_at(page_map, raw, "<b>suġaiḷaq</b>") == "Latin Extended"
    assert _script_at(page_map, raw, "ice") == "Latin"


def test_script_label_for_bucket_unknown():
    assert script_label_for_bucket("unknown_bucket") == "Other"

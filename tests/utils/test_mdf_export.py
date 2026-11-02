"""Tests for MDF export post-processing."""

from mudidi.utils.mdf_export import normalize_mdf_field_value, normalize_mdf_text


def test_normalize_mdf_text_strips_leaked_bold_and_italic() -> None:
    raw = (
        "\\lx <b>апԓёратвака</b>\n"
        "\\gn че́рез <i>не́которое</i> вре́мя\n"
        "\n"
        "\\lx plain"
    )
    out = normalize_mdf_text(raw)
    assert "\\lx апԓёратвака" in out
    assert "\\gn че́рез не́которое вре́мя" in out
    assert "<b>" not in out
    assert "<i>" not in out
    assert "\\lx plain" in out


def test_normalize_mdf_field_value_preserves_characters() -> None:
    assert normalize_mdf_field_value("<b>lemma</b>") == "lemma"
    assert normalize_mdf_field_value("gloss.") == "gloss"


def test_normalize_treats_unmarked_lines_as_part_of_the_field_above() -> None:
    # A page may open with the rest of a field the previous page left open.
    raw = "uä <i>etc</i>.\n\\se habayta.\n\\ge nach Hause,\nzu Hause.\n\n\\lx x\n."

    assert normalize_mdf_text(raw) == (
        "uä etc\n"  # opening continuation: markup and final punctuation stripped
        "\\se habayta\n"
        "\\ge nach Hause,\n"  # field continues: no end-of-sentence stripping here
        "zu Hause\n"
        "\n"
        "\\lx x"  # a continuation that was only punctuation is dropped
    )

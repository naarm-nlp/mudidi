"""Unit tests for Tier-2 tag-injection recovery + labeler helpers (no live LLM)."""

from __future__ import annotations

import inspect

import pytest

import tier2_labeler  # noqa: E402  (module handle for monkeypatching the LLM)
from label_studio_ner import page_map_to_ls_task, ls_task_to_page_map  # noqa: E402
from tier2_labeler import (  # noqa: E402
    _strip_code_fence,
    apply_code_overrides,
    build_legend_prompt,
    build_prompt,
    build_tagging_prompt,
    dictionary_legend_path,
    label_dictionary,
    load_legend,
    parse_legend_output,
    read_dictionary_rules,
    read_language_seed,
    resolve_legend_path,
    save_legend,
)
from tier2_recovery import (  # noqa: E402
    Tier2DriftError,
    detect_markup_tags,
    parse_tagged,
    recover_page_map,
)

from mudidi.schemas.language_span import SPACE  # noqa: E402


def test_call_llm_omits_temperature_from_generic_completion_request(monkeypatch):
    captured = {}

    def fake_complete_with_usage(**kwargs):
        captured.update(kwargs)
        return "answer", {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_complete_with_usage)

    tier2_labeler._call_llm(
        "prompt",
        model="gemini/gemini-3-flash-preview",
        reasoning_effort="none",
        max_tokens=16,
    )
    assert "temperature" not in captured

@pytest.mark.parametrize(
    "function_name",
    ["_call_llm", "run_legend_stage", "run_tagging_stage", "label_page", "label_dictionary"],
)
def test_labeler_apis_expose_no_temperature_parameter(function_name: str) -> None:
    function = getattr(tier2_labeler, function_name)

    assert "temperature" not in inspect.signature(function).parameters


def test_labeler_cli_rejects_removed_temperature_option(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        tier2_labeler.main(
            [
                "--dictionaries-root",
                str(tmp_path),
                "--temperature",
                "0.2",
            ]
        )

    assert exc_info.value.code == 2
    assert "unrecognized arguments: --temperature" in capsys.readouterr().err



def _lang_at(page_map, raw, needle):
    start = raw.index(needle)
    cm = page_map.language_char_map(raw)
    # Whitespace is SPACE by design; check the language of the non-space codepoints.
    langs = {cm[i] for i in range(start, start + len(needle)) if not raw[i].isspace()}
    assert len(langs) == 1, f"{needle!r} spans multiple languages: {langs}"
    return next(iter(langs))


def test_exact_round_trip_partitions_languages():
    raw = "akɔɔtee small crab\n"
    tagged = "<Canala>akɔɔtee</Canala> <English>small crab</English>\n"
    pm, used, drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=1)
    assert drift == 0.0
    pm.validate_against(raw)
    assert _lang_at(pm, raw, "akɔɔtee") == "Canala"
    assert _lang_at(pm, raw, "small crab") == "English"
    assert set(used) == {"Canala", "English"}


def test_markup_preserved_as_content():
    raw = "<b>akɔɔtee</b> small\n"
    tagged = "<Canala><b>akɔɔtee</b></Canala> <English>small</English>\n"
    pm, _used, drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=1)
    assert drift == 0.0
    # The <b>...</b> markup is content inside the Canala span, not a language tag.
    assert _lang_at(pm, raw, "<b>akɔɔtee</b>") == "Canala"


def test_open_vocabulary_discovery():
    raw = "amãrɛ the remains; tourterelle verte\n"
    tagged = (
        "<Canala>amãrɛ</Canala> <English>the remains;</English> "
        "<French>tourterelle verte</French>\n"
    )
    pm, used, drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=2)
    assert drift == 0.0
    assert "French" in used  # not in the seed, still recovered
    assert _lang_at(pm, raw, "tourterelle verte") == "French"


def test_whitespace_forced_to_space():
    raw = "akɔɔtee small\n"
    # Even if the model tucks the space inside a tag, whitespace becomes SPACE.
    tagged = "<Canala>akɔɔtee </Canala><English>small</English>\n"
    pm, _used, _drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=1)
    cm = pm.language_char_map(raw)
    assert cm[raw.index(" ")] == SPACE
    assert cm[raw.index("\n")] == SPACE


def test_untagged_content_is_space():
    raw = "akɔɔtee small\n"
    tagged = "<Canala>akɔɔtee</Canala> small\n"  # 'small' left untagged
    pm, _used, _drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=1)
    assert _lang_at(pm, raw, "small") == SPACE


def test_minor_drift_recovers_within_tolerance():
    raw = "akɔɔtee small crab\n"
    tagged = "<Canala>akɔɔtee</Canala> <English>small crb</English>\n"  # dropped 'a'
    pm, _used, drift = recover_page_map(
        raw, tagged, dictionary="Canala-English", page=1, max_drift=0.5
    )
    assert drift > 0.0
    pm.validate_against(raw)
    assert _lang_at(pm, raw, "akɔɔtee") == "Canala"


def test_excess_drift_rejected():
    raw = "akɔɔtee small crab\n"
    tagged = "<Canala>akɔɔtee</Canala> <English>entirely different text</English>\n"
    with pytest.raises(Tier2DriftError):
        recover_page_map(raw, tagged, dictionary="Canala-English", page=1)


def test_ner_round_trip_is_lossless():
    raw = "<b>amãrɛ</b> the remains; tourterelle verte\n"
    tagged = (
        "<Canala><b>amãrɛ</b></Canala> <English>the remains;</English> "
        "<French>tourterelle verte</French>\n"
    )
    pm, _used, _drift = recover_page_map(raw, tagged, dictionary="Canala-English", page=2)
    task = page_map_to_ls_task(pm, raw)
    rebuilt = ls_task_to_page_map(
        task, raw, dictionary="Canala-English", page=2, labeled_via="llm"
    )
    assert rebuilt.canonical().spans == pm.canonical().spans


def test_span_tags_in_gold_treated_as_content():
    # <span> appears in the Nahuatl-French gold as literal content (not a language).
    # detect_markup_tags must include it so parse_tagged preserves it verbatim.
    raw = "<span>CENTLACOL</span> or something\n"
    markup = detect_markup_tags(raw)
    assert "span" in markup
    tagged = "<fra><span>CENTLACOL</span></fra> <fra>or something</fra>\n"
    pm, used, drift = recover_page_map(raw, tagged, dictionary="Nahuatl-French", page=74,
                                       markup_tags=markup, code_to_language={"fra": "French"})
    assert drift == 0.0
    pm.validate_against(raw)


def test_parse_tagged_stray_close_ignored():
    text, langs, used = parse_tagged("<English>hi</English></French>!")
    assert text == "hi!"
    assert used == {"English"}
    assert langs[0] == "English" and langs[-1] == SPACE  # '!' after close -> SPACE


def test_strip_code_fence():
    fenced = "```\n<English>hi</English>\n```"
    assert _strip_code_fence(fenced) == "<English>hi</English>"
    plain = "<English>hi</English>"
    assert _strip_code_fence(plain) == plain


def test_build_prompt_lists_seed_and_text():
    prompt = build_prompt("akɔɔtee small\n", ["Canala", "English", "French"])
    assert "Canala, English, French" in prompt
    assert "Language-Script" in prompt
    assert "Japanese-Hiragana" in prompt
    assert "akɔɔtee small" in prompt
    assert "Do NOT add, delete" in prompt


def test_read_language_seed_merges_rules(tmp_path):
    (tmp_path / "dictionary_languages.yaml").write_text(
        "source:\n  language: Canala\ntargets:\n- language: English\n",
        encoding="utf-8",
    )
    (tmp_path / "language_rules.yaml").write_text(
        "languages:\n- French\n", encoding="utf-8"
    )
    assert read_language_seed(tmp_path) == ["Canala", "English", "French"]


def test_read_dictionary_rules_loads_text_and_overrides(tmp_path):
    (tmp_path / "language_rules.yaml").write_text(
        "languages:\n- French\n"
        "rules: |\n"
        "  Treat abbreviations as meta.\n"
        "code_overrides:\n"
        "  orok: Evenki\n",
        encoding="utf-8",
    )
    (tmp_path / "extra.md").write_text("Only use Evenki and Russian.", encoding="utf-8")
    rules = read_dictionary_rules(tmp_path, [tmp_path / "extra.md"])
    assert rules.extra_languages == ("French",)
    assert "Treat abbreviations as meta." in rules.rules_text
    assert "Only use Evenki and Russian." in rules.rules_text
    assert rules.code_overrides == {"orok": "Evenki"}


def test_apply_code_overrides_renames_legend_entries():
    legend = {"orok": "Orok", "rus": "Russian"}
    merged = apply_code_overrides(legend, {"orok": "Evenki"})
    assert merged == {"orok": "Evenki", "rus": "Russian"}


def test_parse_legend_output_parses_codes():
    out = "LANGUAGES:\neng-lat = English-Latin\njph = Japanese-Hiragana\n"
    legend, block = parse_legend_output(out)
    assert legend == {"eng-lat": "English-Latin", "jph": "Japanese-Hiragana"}
    assert "eng-lat = English-Latin" in block


def test_recover_compound_language_script_labels():
    raw = "こんにちは HELLO\n"
    tagged = "<jph>こんにちは</jph> <eng-lat>HELLO</eng-lat>\n"
    legend = {"jph": "Japanese-Hiragana", "eng-lat": "English-Latin"}
    pm, used, drift = recover_page_map(
        raw, tagged, dictionary="Japanese-English", page=1, code_to_language=legend
    )
    assert drift == 0.0
    assert _lang_at(pm, raw, "こんにちは") == "Japanese-Hiragana"
    assert _lang_at(pm, raw, "HELLO") == "English-Latin"
    assert set(used) == {"Japanese-Hiragana", "English-Latin"}


def test_build_legend_prompt_includes_rules():
    prompt = build_legend_prompt(
        "hello\n",
        ["Evenki", "Russian"],
        rules_text="Only use Evenki and Russian.",
    )
    assert "Only use Evenki and Russian." in prompt
    assert "Do NOT output a TAGGED: block" in prompt


def test_build_tagging_prompt_uses_fixed_legend():
    prompt = build_tagging_prompt(
        "hello\n",
        {"evn": "Evenki", "rus": "Russian"},
        rules_text="Dialect abbreviations are meta.",
    )
    assert "evn = Evenki" in prompt
    assert "Use ONLY the short codes listed above" in prompt
    assert "Dialect abbreviations are meta." in prompt


def test_save_and_load_legend_round_trip(tmp_path):
    legend_path = tmp_path / "page_1_legend.yaml"
    save_legend(legend_path, {"orok": "Evenki", "rus": "Russian"}, raw_text="raw")
    assert load_legend(legend_path) == {"orok": "Evenki", "rus": "Russian"}
    assert (tmp_path / "page_1_legend.raw.txt").read_text(encoding="utf-8") == "raw"


def test_save_dictionary_legend_raw_txt(tmp_path):
    legend_path = tmp_path / "language_legend.yaml"
    save_legend(legend_path, {"evn": "Evenki"}, raw_text="raw")
    assert (tmp_path / "language_legend.raw.txt").read_text(encoding="utf-8") == "raw"


def test_resolve_legend_path_prefers_dictionary(tmp_path):
    dictionary_dir = _make_dictionary(tmp_path, {1: "hello\n"})
    out_root = tmp_path / "outputs"
    gold = next((dictionary_dir / "Stage 1 Gold OCR").rglob("*_GOLD_flat.txt"))
    dict_legend = dictionary_legend_path(dictionary_dir)
    page_legend = out_root / "Canala-English" / "page_1_legend.yaml"
    save_legend(dict_legend, {"can": "Canala"})
    save_legend(page_legend, {"eng": "English"})
    resolved = resolve_legend_path(dictionary_dir, gold, out_root)
    assert resolved == dict_legend


def test_resolve_legend_path_falls_back_to_page_legend(tmp_path):
    dictionary_dir = _make_dictionary(tmp_path, {1: "hello\n"})
    out_root = tmp_path / "outputs"
    gold = next((dictionary_dir / "Stage 1 Gold OCR").rglob("*_GOLD_flat.txt"))
    page_legend = out_root / "Canala-English" / "page_1_legend.yaml"
    save_legend(page_legend, {"can": "Canala"})
    resolved = resolve_legend_path(dictionary_dir, gold, out_root)
    assert resolved == page_legend


def test_label_dictionary_legend_stage_writes_yaml(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})

    monkeypatch.setattr(
        tier2_labeler,
        "complete_with_usage",
        lambda **kwargs: ("LANGUAGES:\ncan = Canala\neng = English\n", {}),
    )
    results = label_dictionary(
        dictionary_dir,
        output_root=tmp_path / "outputs",
        stage="legend",
    )
    assert results[0].status == "legend_ok"
    legend_path = dictionary_dir / "language_legend.yaml"
    assert legend_path.is_file()
    assert load_legend(legend_path) == {"can": "Canala", "eng": "English"}


def test_label_dictionary_legend_stage_page_scope_writes_per_page(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})

    monkeypatch.setattr(
        tier2_labeler,
        "complete_with_usage",
        lambda **kwargs: ("LANGUAGES:\ncan = Canala\neng = English\n", {}),
    )
    results = label_dictionary(
        dictionary_dir,
        output_root=tmp_path / "outputs",
        stage="legend",
        legend_scope="page",
    )
    assert results[0].status == "legend_ok"
    legend_path = tmp_path / "outputs" / "Canala-English" / "page_1_legend.yaml"
    assert legend_path.is_file()


def test_label_dictionary_tagging_stage_requires_legend(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})
    out_root = tmp_path / "outputs"

    calls = []

    def fake_llm(**kwargs):
        calls.append(1)
        return "TAGGED:\n<can>akɔɔtee</can> <eng>small</eng>\n", {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_llm)
    results = label_dictionary(dictionary_dir, output_root=out_root, stage="tagging")
    assert results[0].status == "failed"
    assert "missing legend file" in (results[0].error or "")
    assert calls == []


def test_label_dictionary_tagging_stage_uses_saved_legend(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})
    out_root = tmp_path / "outputs"
    save_legend(
        dictionary_dir / "language_legend.yaml",
        {"can": "Canala", "eng": "English"},
    )

    monkeypatch.setattr(
        tier2_labeler,
        "complete_with_usage",
        lambda **kwargs: ("TAGGED:\n<can>akɔɔtee</can> <eng>small</eng>\n", {}),
    )
    results = label_dictionary(
        dictionary_dir,
        output_root=out_root,
        stage="tagging",
    )
    assert results[0].status == "ok"
    assert results[0].page_map is not None
    assert _lang_at(results[0].page_map, "akɔɔtee small\n", "akɔɔtee") == "Canala"


def _make_dictionary(root, pages):
    """Build a minimal Canala-English gold dictionary; ``pages`` is {page_no: text}."""
    dictionary_dir = root / "Canala-English"
    dictionary_dir.mkdir()
    (dictionary_dir / "dictionary_languages.yaml").write_text(
        "source:\n  language: Canala\ntargets:\n- language: English\n", encoding="utf-8"
    )
    for page, text in pages.items():
        gold_dir = dictionary_dir / "Stage 1 Gold OCR" / f"sub{page}"
        gold_dir.mkdir(parents=True)
        (gold_dir / f"page_{page}_stage1_GOLD_flat.txt").write_text(
            text, encoding="utf-8"
        )
    return dictionary_dir


def test_label_dictionary_continues_past_failed_page(tmp_path, monkeypatch):
    # Page 1's LLM output round-trips cleanly; page 2 drifts past the gate. The
    # drifted page must be recorded as failed WITHOUT aborting the whole batch.
    dictionary_dir = _make_dictionary(
        tmp_path, {1: "akɔɔtee small\n", 2: "amãrɛ crab\n"}
    )

    def fake_llm(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "akɔɔtee" in prompt:
            return "<Canala>akɔɔtee</Canala> <English>small</English>\n", {}
        return "<English>completely unrelated replacement sentence</English>\n", {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_llm)
    results = label_dictionary(dictionary_dir, output_root=tmp_path / "outputs")
    by_page = {r.page: r for r in results}
    assert by_page[1].status == "ok"
    assert by_page[2].status == "failed"
    assert "Tier2DriftError" in (by_page[2].error or "")


def test_label_dictionary_writes_under_output_root_subdir(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})
    out_root = tmp_path / "outputs"

    monkeypatch.setattr(
        tier2_labeler,
        "complete_with_usage",
        lambda **kwargs: ("<Canala>akɔɔtee</Canala> <English>small</English>\n", {}),
    )
    results = label_dictionary(dictionary_dir, output_root=out_root)
    # Path layout is <output_root>/<dictionary>/page_<N>_lang.json (not inside dataset).
    assert results[0].out_path == out_root / "Canala-English" / "page_1_lang.json"


def test_label_dictionary_skip_existing_avoids_llm(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})
    out_root = tmp_path / "outputs"
    gold = next(dictionary_dir.glob("Stage 1 Gold OCR/*/*_stage1_GOLD_flat.txt"))
    existing = tier2_labeler._lang_map_path(gold, out_root)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("{}", encoding="utf-8")

    calls = []

    def fake_llm(**kwargs):
        calls.append(1)
        return "<Canala>akɔɔtee</Canala> <English>small</English>\n", {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_llm)
    # skip_existing is the default; an existing map is skipped without an LLM call.
    results = label_dictionary(dictionary_dir, output_root=out_root)
    assert results[0].status == "skipped"
    assert results[0].out_path == existing
    assert calls == []


def test_label_dictionary_overwrite_relabels_existing(tmp_path, monkeypatch):
    dictionary_dir = _make_dictionary(tmp_path, {1: "akɔɔtee small\n"})
    out_root = tmp_path / "outputs"
    gold = next(dictionary_dir.glob("Stage 1 Gold OCR/*/*_stage1_GOLD_flat.txt"))
    existing = tier2_labeler._lang_map_path(gold, out_root)
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("{}", encoding="utf-8")

    calls = []

    def fake_llm(**kwargs):
        calls.append(1)
        return "<Canala>akɔɔtee</Canala> <English>small</English>\n", {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_llm)
    # skip_existing=False (CLI --overwrite) re-labels the page despite the existing map.
    results = label_dictionary(dictionary_dir, skip_existing=False, output_root=out_root)
    assert results[0].status == "ok"
    assert calls == [1]  # LLM was called


def test_parse_llm_output_splits_legend_and_tagged():
    out = (
        "LANGUAGES:\n"
        "ike = Iñupiatun Eskimo\n"
        "eng = English\n"
        "\n"
        "TAGGED:\n"
        "<ike>Qiñu</ike> <eng>ice</eng>\n"
    )
    legend, tagged = tier2_labeler.parse_llm_output(out)
    assert legend == {"ike": "Iñupiatun Eskimo", "eng": "English"}
    assert tagged == "<ike>Qiñu</ike> <eng>ice</eng>\n"


def test_parse_llm_output_without_markers_is_all_tagged():
    legend, tagged = tier2_labeler.parse_llm_output("<eng>hi</eng>")
    assert legend == {}
    assert tagged == "<eng>hi</eng>"


def test_recover_page_map_translates_iso_codes_to_names():
    # The multi-word name 'Iñupiatun Eskimo' could never be a tag; via an ISO code it
    # round-trips cleanly and is restored from the legend.
    raw = "Qiñu ice\n"
    tagged = "<ike>Qiñu</ike> <eng>ice</eng>\n"
    legend = {"ike": "Iñupiatun Eskimo", "eng": "English"}
    pm, used, drift = recover_page_map(
        raw, tagged, dictionary="Iñupiatun Eskimo-English", page=42,
        code_to_language=legend,
    )
    assert drift == 0.0
    pm.validate_against(raw)
    assert _lang_at(pm, raw, "Qiñu") == "Iñupiatun Eskimo"
    assert _lang_at(pm, raw, "ice") == "English"
    assert set(used) == {"Iñupiatun Eskimo", "English"}


def test_label_page_maps_iso_codes_end_to_end(monkeypatch):
    raw = "Qiñu ice\n"

    def fake_llm(**kwargs):
        return (
            "LANGUAGES:\nike = Iñupiatun Eskimo\neng = English\n"
            "TAGGED:\n<ike>Qiñu</ike> <eng>ice</eng>\n"
        ), {}

    monkeypatch.setattr(tier2_labeler, "complete_with_usage", fake_llm)
    pm, used, drift, _usage = tier2_labeler.label_page(
        raw,
        dictionary="Iñupiatun Eskimo-English",
        page=42,
        seed_languages=["Iñupiatun Eskimo", "English"],
    )
    assert drift == 0.0
    assert _lang_at(pm, raw, "Qiñu") == "Iñupiatun Eskimo"

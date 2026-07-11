from __future__ import annotations

import argparse
from pathlib import Path

from mudidi.cli import extract


def _batch_args(dataset: Path, output: Path) -> argparse.Namespace:
    return argparse.Namespace(
        samples_dir=str(dataset),
        languages=None,
        output=str(output),
        no_intro=False,
        no_alphabet=False,
    )


def test_dataset_batch_discovers_dictionary_layout_and_preserves_output_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dictionaries"
    entry = dataset / "Evenki-Russian"
    pages = entry / "Dictionary pages"
    pages.mkdir(parents=True)
    alphabet = entry / "Alphabet list" / "alphabet.txt"
    alphabet.parent.mkdir()
    alphabet.write_text("abc", encoding="utf-8")
    languages = entry / "dictionary_languages.yaml"
    languages.write_text("layout: parallel", encoding="utf-8")
    output = tmp_path / "benchmark-output"
    observed = []

    def fake_run_single(args, _parser):
        observed.append(
            (
                args.input_image,
                args.output,
                args.alphabet,
                args.dictionary_languages,
            )
        )
        return 0

    monkeypatch.setattr(extract, "_run_single_entry", fake_run_single)

    result = extract._run_samples_dir(
        _batch_args(dataset, output),
        argparse.ArgumentParser(),
    )

    assert result == 0
    assert observed == [
        (
            str(pages),
            str(output / "Evenki-Russian"),
            str(alphabet),
            str(languages),
        )
    ]


def test_dataset_batch_fails_when_no_entry_has_supported_pages(tmp_path: Path) -> None:
    dataset = tmp_path / "dictionaries"
    (dataset / "Empty-Entry").mkdir(parents=True)

    result = extract._run_samples_dir(
        _batch_args(dataset, tmp_path / "output"),
        argparse.ArgumentParser(),
    )

    assert result == 1


def test_legacy_snippets_batch_uses_configured_output_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "samples"
    snippets = dataset / "Legacy-Language" / "snippets"
    snippets.mkdir(parents=True)
    output = tmp_path / "configured-output"
    observed = []

    def fake_run_single(args, _parser):
        observed.append(args.output)
        return 0

    monkeypatch.setattr(extract, "_run_single_entry", fake_run_single)

    result = extract._run_samples_dir(
        _batch_args(dataset, output),
        argparse.ArgumentParser(),
    )

    assert result == 0
    assert observed == [str(output / "Legacy-Language")]


def test_dataset_batch_prepares_external_stage1_prediction_slot(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    pages = dataset / "Evenki-Russian" / "Dictionary pages"
    pages.mkdir(parents=True)
    prediction_root = tmp_path / "stage1-predictions"
    source_slot = (
        prediction_root
        / "Evenki-Russian"
        / "stage-1"
        / "gemini31pro_flat_alpha"
    )
    source_slot.mkdir(parents=True)
    (source_slot / "run_config.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "stage2-output"
    args = _batch_args(dataset, output)
    args.stage1_predictions_root = str(prediction_root)
    args.stage1_output_subdir = "stage-1"
    args.experiment_name = "gemini31pro_flat_alpha"
    observed = []

    def fake_run_single(run_args, _parser):
        destination = (
            Path(run_args.output)
            / "stage-1"
            / "gemini31pro_flat_alpha"
            / "run_config.json"
        )
        observed.append(destination.is_file())
        return 0

    monkeypatch.setattr(extract, "_run_single_entry", fake_run_single)

    result = extract._run_samples_dir(args, argparse.ArgumentParser())

    assert result == 0
    assert observed == [True]


def test_dataset_batch_uses_prior_experiment_as_per_entry_ocr_hints(
    monkeypatch,
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    pages = dataset / "Evenki-Russian" / "Dictionary pages"
    pages.mkdir(parents=True)
    output = tmp_path / "output"
    hint_dir = output / "Evenki-Russian" / "ocr-hints" / "Mathpix-OCR"
    hint_dir.mkdir(parents=True)
    (hint_dir / "page_1.md").write_text("OCR hint", encoding="utf-8")
    args = _batch_args(dataset, output)
    args.ocr_hint_experiment = "Mathpix-OCR"
    args.no_ocr_hint = False
    observed = []

    def fake_run_single(run_args, _parser):
        observed.append(run_args.ocr_text)
        return 0

    monkeypatch.setattr(extract, "_run_single_entry", fake_run_single)

    result = extract._run_samples_dir(args, argparse.ArgumentParser())

    assert result == 0
    assert observed == [str(hint_dir)]

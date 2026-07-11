"""Stage-1 batch extraction using specialized OCR/VLM models."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mudidi.extraction.sample_entry import (
    configure_benchmark_entry_args,
    report_entry_input_failures,
    validate_configured_sample_entry,
)
from mudidi.ocr.adapters.flat_export import write_stage1_flat_for_page
from mudidi.ocr.vlm.page_inputs import list_snippet_pages, materialize_page_image
from mudidi.ocr.vlm.prompts import (
    build_stage1_context_prompt,
    find_ocr_hint_file,
    load_alphabet_text,
    load_ocr_hint_text,
)
from mudidi.ocr.vlm.runner import VlmOcrRunner, create_vlm_runner, page_is_complete

logger = logging.getLogger(__name__)


def alphabet_disabled_for_experiment(
    experiment_name: str,
    *,
    global_no_alphabet: bool,
) -> bool:
    """Return True when alphabet should be off for one experiment slot."""
    if global_no_alphabet:
        return True
    lower = experiment_name.lower()
    return "noalpha" in lower or "no_alpha" in lower


def _git_short_sha() -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _write_run_config(target_dir: Path, manifest: dict[str, Any], *, force: bool) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "run_config.json"
    if not force and path.exists():
        print(
            f"  Keeping existing {path} (resume; pass --overwrite to refresh it)."
        )
        return
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _build_vlm_manifest(
    args: Any,
    snippets_dir: Path,
    snippets: list[Path],
    spec: Any,
    *,
    ocr_dir: Path | None = None,
) -> dict[str, Any]:
    from mudidi.evaluation.stage1.flatten import FLAT_SPEC_VERSION

    alphabet_path = getattr(args, "alphabet", None)
    return {
        "stage": "1",
        "experiment_name": args.experiment_name,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "strategy": "vlm_ocr",
        "git_sha": _git_short_sha(),
        "vlm_model": spec.key,
        "model_id": spec.model_id,
        "product_label": spec.product_label,
        "flat_spec_version": FLAT_SPEC_VERSION,
        "vlm_dpi": getattr(args, "vlm_dpi", 200),
        "alphabet": {
            "used": bool(alphabet_path),
            "path": alphabet_path,
        },
        "ocr_hint": {
            "used": bool(ocr_dir),
            "dir": str(ocr_dir) if ocr_dir else None,
        },
        "glm_ocr_prompt": getattr(args, "glm_ocr_prompt", None),
        "inputs": {
            "snippets_dir": str(snippets_dir),
            "page_count": len(snippets),
        },
        "per_page": [
            {
                "stem": s.stem,
                "snippet_path": str(s),
                "ocr_hint_file": (
                    str(find_ocr_hint_file(ocr_dir, s.stem))
                    if ocr_dir and find_ocr_hint_file(ocr_dir, s.stem)
                    else None
                ),
            }
            for s in snippets
        ],
    }


def _page_prompt_for_runner(
    runner: VlmOcrRunner,
    args: Any,
    *,
    stem: str,
    ocr_dir: Path | None,
) -> str | None:
    if runner.spec.key != "glm-ocr":
        return None
    alphabet_text = load_alphabet_text(getattr(args, "alphabet", None))
    ocr_file = find_ocr_hint_file(ocr_dir, stem) if ocr_dir else None
    ocr_hint = load_ocr_hint_text(ocr_file)
    return build_stage1_context_prompt(
        alphabet_text=alphabet_text,
        ocr_hint=ocr_hint,
    )


def run_vlm_ocr_entry(
    args: Any,
    input_dir: Path,
    output_dir: Path,
    runner: VlmOcrRunner,
) -> int:
    """Run VLM OCR on all snippets in ``input_dir`` for one dictionary entry.

    Args:
        args: Parsed CLI namespace.
        input_dir: ``snippets/`` directory.
        output_dir: Entry ``outputs/`` directory.
        runner: Loaded VLM runner.

    Returns:
        Exit code 0 on success, 1 if any page failed.
    """
    snippets_dir = input_dir
    snippets = list_snippet_pages(snippets_dir)
    if args.limit:
        snippets = snippets[: args.limit]

    stage1_dir = output_dir / "stage-1" / args.experiment_name
    render_cache = output_dir / ".rendered_snippets"
    dpi = getattr(args, "vlm_dpi", 200)
    ocr_dir = Path(args.ocr_text) if getattr(args, "ocr_text", None) else None

    _write_run_config(
        stage1_dir,
        _build_vlm_manifest(args, snippets_dir, snippets, runner.spec, ocr_dir=ocr_dir),
        force=args.overwrite,
    )

    total = len(snippets)
    skipped = processed = failed = 0

    print(f"\nFound {total} snippet(s) in {snippets_dir}")
    print(
        f"VLM: {runner.spec.product_label} | Output: {stage1_dir} | "
        f"Alphabet: {'on' if getattr(args, 'alphabet', None) else 'off'} | "
        f"OCR hint: {'on' if ocr_dir else 'off'}"
    )

    for idx, snippet in enumerate(snippets):
        stem = snippet.stem
        page_dir = stage1_dir / stem

        if not args.overwrite and page_is_complete(runner, page_dir, stem=stem):
            flat_path = page_dir / f"{stem}_stage1_flat.txt"
            if not flat_path.is_file():
                flat_path = write_stage1_flat_for_page(page_dir, stem=stem)
                print(
                    f"[{idx + 1}/{total}] SKIP {snippet.name} "
                    f"(already complete; backfilled {flat_path.name})"
                )
            else:
                print(f"[{idx + 1}/{total}] SKIP {snippet.name} (already complete)")
            skipped += 1
            continue

        print(f"\n[{idx + 1}/{total}] Processing: {snippet.name}")
        page_dir.mkdir(parents=True, exist_ok=True)

        try:
            started = time.perf_counter()
            image_path = materialize_page_image(
                snippet, render_cache / runner.spec.key, dpi=dpi
            )
            input_copy = page_dir / "input.png"
            if image_path.resolve() != input_copy.resolve():
                import shutil

                shutil.copy2(image_path, input_copy)

            artifacts = runner.run_page(
                image_path,
                page_dir,
                stem=stem,
                prompt=_page_prompt_for_runner(runner, args, stem=stem, ocr_dir=ocr_dir),
            )
            flat_path = write_stage1_flat_for_page(page_dir, stem=stem)
            elapsed = time.perf_counter() - started
            print(
                f"  Done in {elapsed:.1f}s -> {page_dir} "
                f"({', '.join(Path(v).name for v in artifacts.values())}, "
                f"{flat_path.name})"
            )
            processed += 1
        except Exception as exc:
            logger.exception("VLM OCR failed for %s", snippet.name)
            print(f"  ERROR: {exc}")
            failed += 1

    print(
        f"\nVLM OCR summary: {processed} processed, {skipped} skipped, {failed} failed."
    )
    return 0 if failed == 0 else 1


def run_vlm_ocr_batch(args: Any, entries: list[Path]) -> int:
    """Process multiple language entries and experiments with one loaded VLM."""
    from mudidi.ocr.vlm.paddle_genai_server import ensure_paddle_vllm_server_args

    experiment_names: list[str] = getattr(args, "experiment_names", None) or [
        args.experiment_name
    ]
    global_no_alphabet = getattr(args, "no_alphabet", False)
    saved_experiment_name = args.experiment_name
    saved_no_alphabet = global_no_alphabet
    output_root = Path(args.output)

    paddle_server: Any = None
    glm_server: Any = None
    try:
        paddle_server = ensure_paddle_vllm_server_args(args)
        if paddle_server is not None:
            print(
                f"Paddle GenAI vLLM server: {args.paddle_vl_rec_server_url} "
                f"(auto-started; stops when this run finishes)"
            )
        from mudidi.ocr.vlm.glm_vllm_server import ensure_glm_vllm_server_args

        glm_server = ensure_glm_vllm_server_args(args)
        if glm_server is not None:
            print(
                f"GLM-OCR vLLM server: {args.glm_vllm_server_url} "
                f"(auto-started; stops when this run finishes)"
            )

        runner = create_vlm_runner(
            args.vlm_model,
            glm_prompt=getattr(args, "glm_ocr_prompt", None),
            glm_max_new_tokens=getattr(args, "glm_max_new_tokens", None),
            glm_backend=getattr(args, "glm_backend", None),
            glm_vllm_server_url=getattr(args, "glm_vllm_server_url", None),
            mineru_backend=getattr(args, "vlm_backend", None),
            mineru_batch_size=getattr(args, "mineru_batch_size", None),
            mineru_max_new_tokens=getattr(args, "mineru_max_new_tokens", None),
            paddle_vl_rec_backend=getattr(args, "paddle_vl_rec_backend", None),
            paddle_vl_rec_server_url=getattr(args, "paddle_vl_rec_server_url", None),
        )
        runner.load()
        any_failure = False
        try:
            for experiment_name in experiment_names:
                args.experiment_name = experiment_name
                args.no_alphabet = alphabet_disabled_for_experiment(
                    experiment_name,
                    global_no_alphabet=global_no_alphabet,
                )
                if len(experiment_names) > 1:
                    print("\n" + "=" * 60)
                    print(f" Experiment: {experiment_name}")
                    print(
                        f" Alphabet: {'off' if args.no_alphabet else 'on'} "
                        f"(model stays loaded)"
                    )
                    print("=" * 60)

                for entry_dir in entries:
                    snippets_dir, output_dir = configure_benchmark_entry_args(
                        args, entry_dir, output_root
                    )
                    if not snippets_dir.is_dir():
                        print(f"[skip] {entry_dir.name}: no snippets/ folder")
                        continue
                    input_errors = validate_configured_sample_entry(
                        args, entry_dir, snippets_dir
                    )
                    if input_errors:
                        report_entry_input_failures(
                            entry_dir.name,
                            input_errors,
                            experiment_name=experiment_name,
                        )
                        any_failure = True
                        continue
                    print("\n" + "#" * 60)
                    print(f"# Entry: {entry_dir.name} | Experiment: {experiment_name}")
                    print("#" * 60)
                    rc = run_vlm_ocr_entry(
                        args,
                        snippets_dir,
                        output_dir,
                        runner,
                    )
                    if rc != 0:
                        any_failure = True
        finally:
            args.experiment_name = saved_experiment_name
            args.no_alphabet = saved_no_alphabet
            runner.unload()
        return 1 if any_failure else 0
    finally:
        if paddle_server is not None:
            paddle_server.stop()
        if glm_server is not None:
            glm_server.stop()

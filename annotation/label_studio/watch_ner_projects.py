"""Keep existing Label Studio NER projects in sync with local span maps.

New pages are appended. Predictions for existing pages are refreshed only when
the task has no submitted annotation; reviewed tasks are never mutated.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

import requests
from pydantic import ValidationError

from label_studio_ner import build_labels_config, page_map_to_ls_task
from setup_ner_projects import (
    _LABEL_VALUE_RE,
    _PROJECT_PREFIX,
    LabelStudioClient,
    find_raw_gold,
    resolve_page_image,
)
from span_schema import PageLanguageMap, sha256_of

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _page_name(task: dict) -> str:
    return str((task.get("data") or {}).get("page_name", ""))


def _has_annotations(task: dict) -> bool:
    return bool(task.get("annotations"))


def _result_signature(results: list[dict]) -> list[tuple[object, ...]]:
    signature = []
    for result in results:
        value = result.get("value") or {}
        signature.append(
            (
                result.get("from_name"),
                result.get("to_name"),
                result.get("type"),
                value.get("start"),
                value.get("end"),
                value.get("text"),
                tuple(value.get("labels") or []),
            )
        )
    return signature


def _prediction_matches(task: dict, desired: dict) -> bool:
    predictions = [
        item for item in task.get("predictions", []) if isinstance(item, dict)
    ]
    desired_predictions = desired.get("predictions") or []
    if not predictions or not desired_predictions:
        return False
    return _result_signature(predictions[-1].get("result") or []) == _result_signature(
        desired_predictions[-1].get("result") or []
    )


def _build_task(
    map_path: Path,
    dictionaries_root: Path,
    render_root: Path,
    document_root: Path,
) -> tuple[str, dict] | None:
    try:
        page_map = PageLanguageMap.model_validate_json(
            map_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as error:
        logger.warning("Skipping invalid span map %s: %s", map_path, error)
        return None
    gold_path = find_raw_gold(dictionaries_root / page_map.dictionary, page_map.page)
    if gold_path is None:
        logger.warning("Skipping %s: gold page not found", map_path)
        return None
    raw_text = gold_path.read_text(encoding="utf-8")
    if sha256_of(raw_text) != page_map.source_text_sha:
        logger.warning("Skipping %s: gold SHA mismatch", map_path)
        return None
    task = page_map_to_ls_task(page_map, raw_text)
    image_url = resolve_page_image(
        dictionaries_root / page_map.dictionary,
        page_map.page,
        (render_root / page_map.dictionary).resolve(),
        document_root=document_root,
    )
    if image_url is not None:
        task["data"]["image_url"] = image_url
    return page_map.dictionary, task


def sync_paths(
    client: LabelStudioClient,
    paths: list[Path],
    dictionaries_root: Path,
    render_root: Path,
    document_root: Path,
) -> None:
    """Reconcile the supplied span maps with existing Label Studio projects."""
    desired_by_dictionary: dict[str, list[dict]] = defaultdict(list)
    for map_path in paths:
        built = _build_task(map_path, dictionaries_root, render_root, document_root)
        if built is not None:
            dictionary, task = built
            desired_by_dictionary[dictionary].append(task)

    projects = {
        project.get("title"): project
        for project in client.list_projects()
        if str(project.get("title", "")).startswith(_PROJECT_PREFIX)
    }
    for dictionary, desired_tasks in desired_by_dictionary.items():
        project = projects.get(f"{_PROJECT_PREFIX}{dictionary}")
        if project is None:
            logger.warning(
                "%s: project does not exist; watcher cannot append pages", dictionary
            )
            continue
        project_id = int(project["id"])
        configured_labels = set(
            _LABEL_VALUE_RE.findall(str(project.get("label_config", "")))
        )
        desired_labels = {
            label
            for task in desired_tasks
            for prediction in task.get("predictions", [])
            for result in prediction.get("result", [])
            for label in (result.get("value") or {}).get("labels", [])
        }
        missing_labels = desired_labels - configured_labels
        if missing_labels:
            all_labels = [*configured_labels, *sorted(missing_labels)]
            with_image = "<Image " in str(project.get("label_config", ""))
            if not client.update_label_config(
                project_id,
                build_labels_config(all_labels, image=with_image),
            ):
                logger.error(
                    "%s: could not add project labels %s",
                    dictionary,
                    sorted(missing_labels),
                )
                continue
            logger.info(
                "%s: added project labels %s", dictionary, sorted(missing_labels)
            )
        existing = {_page_name(task): task for task in client.export_tasks(project_id)}
        additions: list[dict] = []
        for desired in desired_tasks:
            page_name = _page_name(desired)
            current = existing.get(page_name)
            if current is None:
                additions.append(desired)
                continue
            if _has_annotations(current):
                logger.debug(
                    "%s %s: reviewed task left untouched", dictionary, page_name
                )
                continue
            expanded = dict(current)
            if not any(
                isinstance(item, dict) for item in current.get("predictions", [])
            ):
                expanded["predictions"] = client.list_task_predictions(
                    int(current["id"])
                )
            if _prediction_matches(expanded, desired):
                continue
            client.replace_task_prediction(project_id, current, desired)
            logger.info("%s %s: refreshed prediction", dictionary, page_name)
        if additions:
            client.import_tasks(project_id, additions)
            logger.info("%s: appended %d new page(s)", dictionary, len(additions))


def _snapshot(outputs_root: Path) -> dict[Path, tuple[int, int]]:
    return {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in outputs_root.glob("*/*_lang.json")
    }


def watch(
    client: LabelStudioClient,
    outputs_root: Path,
    dictionaries_root: Path,
    render_root: Path,
    document_root: Path,
    interval: float,
) -> None:
    """Poll for changed span maps and reconcile them until interrupted."""
    previous: dict[Path, tuple[int, int]] = {}
    logger.info("Watching %s for span-map changes", outputs_root)
    while True:
        current = _snapshot(outputs_root)
        changed = [
            path
            for path, fingerprint in current.items()
            if previous.get(path) != fingerprint
        ]
        if changed:
            try:
                sync_paths(
                    client, changed, dictionaries_root, render_root, document_root
                )
            except requests.RequestException as error:
                logger.error("Output sync failed; will retry: %s", error)
                time.sleep(interval)
                continue
        previous = current
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ls-url", default=os.getenv("LABEL_STUDIO_URL", "http://localhost:8080")
    )
    parser.add_argument("--ls-token", default=os.getenv("LABEL_STUDIO_TOKEN"))
    parser.add_argument("--outputs-root", type=Path, default=Path("annotation/outputs"))
    parser.add_argument(
        "--dictionaries-root", type=Path, default=Path("dataset/MUDIDI/dictionaries")
    )
    parser.add_argument(
        "--render-root", type=Path, default=Path(".label-studio-renders")
    )
    parser.add_argument(
        "--document-root",
        type=Path,
        default=Path(os.getenv("LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT", ".")),
    )
    parser.add_argument("--interval", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.ls_token:
        logger.error("No Label Studio token provided")
        return 1
    try:
        watch(
            LabelStudioClient(args.ls_url, args.ls_token),
            args.outputs_root,
            args.dictionaries_root,
            args.render_root,
            args.document_root,
            args.interval,
        )
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

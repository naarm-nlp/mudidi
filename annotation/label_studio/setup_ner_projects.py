"""Create / update Label Studio NER projects from annotation/outputs *_lang.json maps.

One project per dictionary, one task per page, NER spans as predictions.
Idempotent by default (skips existing projects). Pass --overwrite to recreate.

Usage:
    uv run python annotation/label_studio/setup_ner_projects.py \
        --ls-url http://localhost:8080 \
        --ls-token <your-api-token>

Environment:
    LABEL_STUDIO_URL    — Label Studio base URL (default: http://localhost:8080)
    LABEL_STUDIO_TOKEN  — API token (legacy Token or PAT)
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

import requests

# Flat-sibling imports: add annotation/label_studio/ to path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from label_studio_ner import build_labels_config, load_label_set, page_map_to_ls_task  # noqa: E402
from span_schema import META, PageLanguageMap, sha256_of  # noqa: E402

_LABEL_VALUE_RE = re.compile(r'<Label value="([^"]+)"')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_PROJECT_PREFIX = "NER: "


# ---------------------------------------------------------------------------
# Minimal Label Studio HTTP client
# ---------------------------------------------------------------------------


class LabelStudioClient:
    """Thin wrapper around the Label Studio HTTP API.

    Auth order (mirrors label-studio/setup.py):
      1. If token looks like a JWT (starts with "eyJ"): PAT flow — exchange
         via POST /api/token/refresh/ for a short-lived access token, then
         use Bearer auth. Re-exchange on any subsequent 401.
      2. Otherwise: try "Token <token>", fall back to "Bearer <token>" on 401.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._refresh_token = token
        self.session = requests.Session()
        self._tried_bearer = False
        self._pat_attempts = 0

        if token.startswith("eyJ"):
            # JWT refresh token — exchange immediately.
            self._refresh_pat()
        else:
            self.session.headers.update({"Authorization": f"Token {token}"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _refresh_pat(self) -> None:
        resp = self.session.post(
            self._url("/api/token/refresh/"),
            json={"refresh": self._refresh_token},
            timeout=15,
        )
        resp.raise_for_status()
        access = resp.json().get("access")
        if not access:
            raise RuntimeError("PAT refresh response missing 'access' field")
        self.session.headers.update({"Authorization": f"Bearer {access}"})

    def _request(self, method: str, path: str, **kwargs: object) -> requests.Response:
        resp = self.session.request(method, self._url(path), **kwargs)
        if resp.status_code == 401:
            if self._refresh_token.startswith("eyJ") and self._pat_attempts < 2:
                # Re-exchange the PAT for a fresh access token and retry once.
                self._pat_attempts += 1
                self._refresh_pat()
                resp = self.session.request(method, self._url(path), **kwargs)
            elif not self._tried_bearer:
                self._tried_bearer = True
                self.session.headers.update(
                    {"Authorization": f"Bearer {self._refresh_token}"}
                )
                resp = self.session.request(method, self._url(path), **kwargs)
        return resp

    def list_projects(self) -> list[dict]:
        resp = self._request("GET", "/api/projects/", params={"page_size": 1000})
        resp.raise_for_status()
        return resp.json().get("results", [])

    def delete_project(self, project_id: int) -> None:
        self._request("DELETE", f"/api/projects/{project_id}/").raise_for_status()

    def create_project(
        self, title: str, label_config: str, description: str = ""
    ) -> dict:
        resp = self._request(
            "POST",
            "/api/projects/",
            json={
                "title": title,
                "description": description,
                "label_config": label_config,
                "is_published": True,
                "show_skip_button": True,
                "enable_empty_annotation": True,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def import_tasks(self, project_id: int, tasks: list[dict]) -> dict:
        resp = self._request("POST", f"/api/projects/{project_id}/import", json=tasks)
        resp.raise_for_status()
        return resp.json()

    def replace_task_prediction(
        self, project_id: int, task: dict, desired: dict
    ) -> None:
        """Replace an unreviewed task's data and predictions without deleting the task."""
        task_id = int(task["id"])
        response = self._request(
            "PATCH", f"/api/tasks/{task_id}/", json={"data": desired["data"]}
        )
        response.raise_for_status()
        prediction = dict(desired["predictions"][-1])
        prediction.update({"task": task_id, "project": project_id})
        response = self._request("POST", "/api/predictions/", json=prediction)
        response.raise_for_status()
        new_prediction_id = response.json().get("id")
        for existing in task.get("predictions", []):
            prediction_id = (
                existing.get("id") if isinstance(existing, dict) else existing
            )
            if prediction_id and prediction_id != new_prediction_id:
                self._request(
                    "DELETE", f"/api/predictions/{prediction_id}/"
                ).raise_for_status()

    def list_task_predictions(self, task_id: int) -> list[dict]:
        """Return expanded predictions for one task."""
        response = self._request("GET", "/api/predictions/", params={"task": task_id})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return payload.get("results", [])

    def update_label_config(self, project_id: int, label_config: str) -> bool:
        """PATCH a project's labelling config in place (non-destructive; keeps tasks)."""
        resp = self._request(
            "PATCH", f"/api/projects/{project_id}/", json={"label_config": label_config}
        )
        if resp.ok:
            return True
        logger.error(
            "  config update failed (%s): %s", resp.status_code, resp.text[:300]
        )
        return False

    def export_tasks(self, project_id: int) -> list[dict]:
        """Return the project's tasks (with submitted annotations) as export JSON."""
        resp = self._request(
            "GET",
            f"/api/projects/{project_id}/export",
            params={"exportType": "JSON", "download_all_tasks": "true"},
        )
        resp.raise_for_status()
        return resp.json()

    def create_local_storage(self, project_id: int, path: str) -> bool:
        """Register a Local Files import storage so the page renders can be served.

        Label Studio only serves a ``/data/local-files/`` file when some storage's
        ``path`` is a prefix of the file's directory. ``path`` must be a strict
        subdirectory of LOCAL_FILES_DOCUMENT_ROOT. We do not sync it (tasks are
        imported via the API) — it exists only to satisfy the serving permission.
        """
        resp = self._request(
            "POST",
            "/api/storages/localfiles",
            json={
                "path": path,
                "project": project_id,
                "use_blob_urls": True,
                "title": "Page renders",
            },
        )
        if resp.ok:
            return True
        logger.error(
            "  local storage create failed (%s): %s", resp.status_code, resp.text[:300]
        )
        return False


# ---------------------------------------------------------------------------
# Gold text lookup
# ---------------------------------------------------------------------------


def find_raw_gold(dictionary_dir: Path, page: int) -> Path | None:
    """Return the flat gold text file for a given page, or None if not found."""
    candidates = sorted(
        dictionary_dir.glob(f"Stage 1 Gold OCR/**/page_{page}_stage1_GOLD_flat.txt")
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Original page image lookup (served to Label Studio as a read-only reference)
# ---------------------------------------------------------------------------


def _render_pdf_first_page(pdf_path: Path, out_path: Path, *, dpi: int = 150) -> bool:
    """Render the first page of a PDF to PNG at ``out_path``. Returns success."""
    try:
        import pymupdf  # PyMuPDF — a project dependency (see CLAUDE.md).
    except ImportError:
        logger.error("pymupdf not installed — cannot render PDF page images.")
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        pix = doc.load_page(0).get_pixmap(dpi=dpi)
        pix.save(str(out_path))
    finally:
        doc.close()
    return out_path.is_file()


def resolve_page_image(
    dictionary_dir: Path, page: int, dict_render_dir: Path, *, document_root: Path
) -> str | None:
    """Materialize a page's image as a PNG and return its ``$image_url``, or None.

    Both PNG and PDF sources are normalized into a single per-dictionary render dir
    (``dict_render_dir/page_<page>.png``) — PNGs are copied, PDFs rendered. Label
    Studio only serves a local file when a ``LocalFilesImportStorage`` covers its
    directory (see ``io_storages/localfiles/views.py``), so collapsing every page
    into one dir lets a single registered storage cover the whole project.

    The returned URL is ``/data/local-files/?d=<path>`` with the path relative to
    ``document_root`` (LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT), URL-encoded.
    """
    pages_dir = dictionary_dir / "Dictionary pages"
    png = pages_dir / f"page_{page}.png"
    pdf = pages_dir / f"page_{page}.pdf"
    out = dict_render_dir / f"page_{page}.png"

    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        if png.is_file():
            shutil.copy2(png, out)
        elif pdf.is_file():
            if not _render_pdf_first_page(pdf, out):
                return None
        else:
            return None

    try:
        rel = out.resolve().relative_to(document_root.resolve())
    except ValueError:
        logger.warning(
            "  page %d render %s is outside the document root %s — no reference shown",
            page,
            out,
            document_root,
        )
        return None
    return "/data/local-files/?d=" + urllib.parse.quote(str(rel))


# ---------------------------------------------------------------------------
# Non-destructive recolor (PATCH label config in place, keep annotations)
# ---------------------------------------------------------------------------


def recolor_project(client: LabelStudioClient, dict_name: str, project: dict) -> bool:
    """Re-emit a project's label config with distinct colours, preserving its tasks.

    The label *values* and image panel are taken from the project's current config, so
    only the ``background`` colours change — existing annotations stay valid.
    """
    cfg = project.get("label_config", "")
    labels = [v for v in _LABEL_VALUE_RE.findall(cfg) if v != META]
    if not labels:
        logger.warning("%s: no labels found in config — skipping recolor", dict_name)
        return False
    new_cfg = build_labels_config(labels, image="<Image" in cfg)
    if new_cfg == cfg:
        logger.info("%s: colours already up to date — skipping", dict_name)
        return False
    if client.update_label_config(project["id"], new_cfg):
        logger.info("%s: recolored %d labels in place", dict_name, len(labels) + 1)
        return True
    return False


# ---------------------------------------------------------------------------
# Per-dictionary project setup
# ---------------------------------------------------------------------------


def setup_dictionary(
    client: LabelStudioClient,
    dict_name: str,
    lang_json_paths: list[Path],
    dictionaries_root: Path,
    *,
    existing_by_title: dict[str, int],
    overwrite: bool,
    render_root: Path,
    document_root: Path,
) -> bool:
    """Create (or skip) a NER project for one dictionary. Returns True if set up."""
    title = f"{_PROJECT_PREFIX}{dict_name}"
    dictionary_dir = dictionaries_root / dict_name
    dict_render_dir = (render_root / dict_name).resolve()

    # Build (page_map, raw_text) pairs, skipping pages whose gold is missing/changed.
    tasks: list[dict] = []
    missing_gold = 0
    images_found = 0
    for lang_path in sorted(lang_json_paths, key=lambda p: int(p.stem.split("_")[1])):
        page_map = PageLanguageMap.model_validate_json(
            lang_path.read_text(encoding="utf-8")
        )
        gold_path = find_raw_gold(dictionary_dir, page_map.page)
        if gold_path is None:
            logger.warning("  [skip] page %d: gold text not found", page_map.page)
            missing_gold += 1
            continue
        raw_text = gold_path.read_text(encoding="utf-8")
        if sha256_of(raw_text) != page_map.source_text_sha:
            logger.warning(
                "  [skip] page %d: sha256 mismatch (gold may have changed)",
                page_map.page,
            )
            missing_gold += 1
            continue
        task = page_map_to_ls_task(page_map, raw_text)
        image_url = resolve_page_image(
            dictionary_dir, page_map.page, dict_render_dir, document_root=document_root
        )
        if image_url is not None:
            task["data"]["image_url"] = image_url
            images_found += 1
        tasks.append(task)

    if not tasks:
        logger.warning("%s: no valid tasks — skipping project creation", dict_name)
        return False

    # Only show the image panel when EVERY task has one — otherwise the config's
    # $image_url binding would render a broken image on the imageless pages.
    with_image = images_found == len(tasks)
    if 0 < images_found < len(tasks):
        logger.warning(
            "%s: only %d/%d pages have a reference image — image panel disabled",
            dict_name,
            images_found,
            len(tasks),
        )

    if title in existing_by_title:
        if not overwrite:
            logger.info(
                "%s: project already exists (id=%d) — skipping",
                dict_name,
                existing_by_title[title],
            )
            return True
        logger.info(
            "%s: deleting existing project (id=%d)", dict_name, existing_by_title[title]
        )
        client.delete_project(existing_by_title[title])

    # Merge base languages (from yaml) with any languages actually used in maps.
    base_languages = load_label_set(dictionary_dir)
    map_languages: set[str] = set()
    for task in tasks:
        for pred in task.get("predictions", []):
            for region in pred.get("result", []):
                map_languages.update(region.get("value", {}).get("labels", []))
    all_languages = list(
        dict.fromkeys([*base_languages, *sorted(map_languages - set(base_languages))])
    )
    label_config = build_labels_config(all_languages, image=with_image)

    project = client.create_project(
        title=title,
        label_config=label_config,
        description=f"Language NER annotation for {dict_name} ({len(tasks)} pages).",
    )
    project_id = project["id"]
    logger.info("%s: created project (id=%d)", dict_name, project_id)

    # Register the per-dict render dir as a Local Files storage so its page images
    # are serveable; without this the $image_url panel 404s even with serving on.
    if with_image and client.create_local_storage(project_id, str(dict_render_dir)):
        logger.info("%s: registered page-render storage %s", dict_name, dict_render_dir)

    result = client.import_tasks(project_id, tasks)
    imported = result.get("task_count", len(tasks))
    suffix = f" ({missing_gold} pages skipped — gold not found)" if missing_gold else ""
    logger.info("%s: imported %d tasks%s", dict_name, imported, suffix)
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up Label Studio NER projects from annotation/outputs *_lang.json maps.",
    )
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=Path("annotation/outputs"),
        help="Root of per-dictionary *_lang.json folders (default: annotation/outputs).",
    )
    parser.add_argument(
        "--dictionaries-root",
        type=Path,
        default=Path("dataset/MUDIDI/dictionaries"),
        help="Root holding per-dictionary Stage 1 Gold OCR trees.",
    )
    parser.add_argument(
        "--ls-url",
        default=os.getenv("LABEL_STUDIO_URL", "http://localhost:8080"),
        help="Label Studio base URL.",
    )
    parser.add_argument(
        "--ls-token",
        default=os.getenv("LABEL_STUDIO_TOKEN"),
        help="Label Studio API token (legacy Token or PAT).",
    )
    parser.add_argument(
        "--dictionaries",
        nargs="+",
        metavar="DICT",
        help="Only process these dictionaries (default: all).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete and recreate existing projects.",
    )
    parser.add_argument(
        "--colors-only",
        action="store_true",
        dest="colors_only",
        help="Non-destructive: only re-apply distinct label colours to existing "
        "projects (PATCH label config in place; keeps tasks and annotations).",
    )
    parser.add_argument(
        "--render-root",
        type=Path,
        default=Path(".label-studio-renders"),
        help="Cache dir for PDF-page PNG renders (default: .label-studio-renders).",
    )
    parser.add_argument(
        "--document-root",
        type=Path,
        default=Path(os.getenv("LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT", ".")),
        help="LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT — page image URLs are made "
        "relative to this (default: $LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT or cwd).",
    )
    return parser.parse_args(argv)


def _recolor_only(
    client: LabelStudioClient, projects: list[dict], dictionaries: list[str] | None
) -> int:
    """Re-apply distinct label colours to existing NER projects, in place."""
    requested = set(dictionaries) if dictionaries else None
    targets = []
    for project in projects:
        title = project.get("title", "")
        if not title.startswith(_PROJECT_PREFIX):
            continue
        dict_name = title[len(_PROJECT_PREFIX) :]
        if requested is None or dict_name in requested:
            targets.append((dict_name, project))

    if not targets:
        logger.warning("No matching 'NER: …' projects to recolor.")
        return 0

    recolored = 0
    for dict_name, project in sorted(targets):
        if recolor_project(client, dict_name, project):
            recolored += 1
    logger.info("Done — recolored %d/%d project(s).", recolored, len(targets))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.ls_token:
        logger.error(
            "No Label Studio token. Set LABEL_STUDIO_TOKEN or pass --ls-token.\n"
            "  Get your token at: %s/user/account",
            args.ls_url,
        )
        return 1

    client = LabelStudioClient(args.ls_url, args.ls_token)
    try:
        projects = client.list_projects()
    except requests.HTTPError as exc:
        logger.error("Cannot connect to Label Studio at %s: %s", args.ls_url, exc)
        return 1

    existing_by_title = {p["title"]: p["id"] for p in projects}
    logger.info("Connected — %d existing projects", len(projects))

    if args.colors_only:
        return _recolor_only(client, projects, args.dictionaries)

    outputs_root = args.outputs_root
    if not outputs_root.is_dir():
        logger.error("outputs-root not found: %s", outputs_root)
        return 1

    by_dict: dict[str, list[Path]] = {}
    for lang_path in sorted(outputs_root.rglob("*_lang.json")):
        dict_name = lang_path.parent.name
        by_dict.setdefault(dict_name, []).append(lang_path)

    if args.dictionaries:
        requested = set(args.dictionaries)
        by_dict = {k: v for k, v in by_dict.items() if k in requested}

    if not by_dict:
        logger.warning("No *_lang.json files found under %s", outputs_root)
        return 0

    logger.info(
        "Found %d dictionaries, %d total pages",
        len(by_dict),
        sum(len(v) for v in by_dict.values()),
    )

    # Reverse-alpha order so Label Studio's newest-first dashboard renders A→Z.
    created = 0
    for dict_name in sorted(by_dict, reverse=True):
        ok = setup_dictionary(
            client,
            dict_name,
            by_dict[dict_name],
            args.dictionaries_root,
            existing_by_title=existing_by_title,
            overwrite=args.overwrite,
            render_root=args.render_root,
            document_root=args.document_root,
        )
        if ok:
            created += 1

    logger.info("Done — %d/%d projects set up.", created, len(by_dict))
    return 0


if __name__ == "__main__":
    sys.exit(main())

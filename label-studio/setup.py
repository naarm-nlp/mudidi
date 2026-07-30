"""Set up Label Studio projects for post-editing OCR transcriptions.

Creates one Label Studio project per dictionary language pair, renders
snippet PDFs to PNGs, uploads page images, and imports stage-1 flat
(or column TSV) transcriptions as pre-filled tasks.

Usage:
    conda activate label-studio
    python scripts/label_studio_setup.py \
        --samples-dir assets/dictionaries/samples-2 \
        --ls-url http://localhost:8080 \
        --ls-token <your-legacy-token>

Environment:
    LABEL_STUDIO_URL  — Label Studio base URL (default: http://localhost:8080)
    LABEL_STUDIO_TOKEN — API token (legacy or PAT)
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
AUTH_SCHEMES = {"auto", "Token", "Bearer", "PAT"}

# Per-layout body section schema.
#   layout key  -> [(section title, task-data field name, textarea rows), ...]
# header / footer slots are added separately by _build_label_config.
_LAYOUT_SECTIONS: dict[str, list[tuple[str, str, int]]] = {
    "single":    [("Body", "body_text", 30)],
    "two_col":   [("Left Column", "left_text", 20),
                  ("Right Column", "right_text", 20)],
    "three_col": [("Left Column", "left_text", 18),
                  ("Middle Column", "middle_text", 18),
                  ("Right Column", "right_text", 18)],
}

_LABEL_CONFIG_TEMPLATE = """\
<View>
  <Style>
    .container { display: flex; gap: 16px; height: 90vh; }
    .left-panel { flex: 1; overflow: auto; border: 1px solid #ddd; border-radius: 8px; padding: 8px; background: #fafafa; }
    .right-panel { flex: 1; overflow: auto; }
    .col-header { font-weight: 700; font-size: 14px; margin: 12px 0 4px; color: #333; border-bottom: 2px solid #4a86e8; padding-bottom: 4px; }
    .meta-header { font-weight: 700; font-size: 14px; margin: 12px 0 4px; color: #555; border-bottom: 2px dashed #999; padding-bottom: 4px; }
    .instructions { font-size: 13px; color: #666; margin-bottom: 12px; padding: 8px; background: #fff8e1; border-radius: 4px; border-left: 3px solid #ffc107; }
  </Style>

  <View className="container">
    <!-- Left: original page image -->
    <View className="left-panel">
      <Header value="Original Dictionary Page" size="4"/>
      <Image name="page_image" value="$image_url" zoomControl="true" rotateControl="true"/>
    </View>

    <!-- Right: editable transcription -->
    <View className="right-panel">
      <Header value="OCR Transcription — Post-Edit" size="4"/>
      <View className="instructions">
        <HyperText name="help" value="Compare with the original page on the left. Fix any character errors, missing text, or formatting issues in the transcription boxes below. Leave Header / Footer blank if the page has none."/>
      </View>

      <View className="meta-header">
        <Header value="Header (page-level metadata above the columns)" size="5"/>
      </View>
      <TextArea name="header_text" toName="page_image"
                value="$header_text" rows="3" editable="true"
                maxSubmissions="1" showSubmitButton="false"/>

__BODY_SECTIONS__

      <View className="meta-header">
        <Header value="Footer (page-level metadata below the columns)" size="5"/>
      </View>
      <TextArea name="footer_text" toName="page_image"
                value="$footer_text" rows="3" editable="true"
                maxSubmissions="1" showSubmitButton="false"/>
    </View>
  </View>
</View>
"""


def _build_label_config(layout: str) -> str:
    """Assemble a Label Studio XML config for the given page layout."""
    sections = _LAYOUT_SECTIONS[layout]
    body_xml = "\n\n".join(
        f'      <View className="col-header">\n'
        f'        <Header value="{title}" size="5"/>\n'
        f'      </View>\n'
        f'      <TextArea name="{field}" toName="page_image"\n'
        f'                value="${field}" rows="{rows}" editable="true"\n'
        f'                maxSubmissions="1" showSubmitButton="false"/>'
        for title, field, rows in sections
    )
    return _LABEL_CONFIG_TEMPLATE.replace("__BODY_SECTIONS__", body_xml)


def _render_pdf_to_png(pdf_path: Path, output_dir: Path, dpi: int = 200) -> list[Path]:
    """Render each page of a PDF to PNG. Returns list of output paths.

    Complexity: O(n) where n is the number of pages in the PDF.
    """
    try:
        import pymupdf
    except ImportError:
        logger.error("pymupdf not installed — run: pip install pymupdf")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    results: list[Path] = []
    try:
        for page_idx in range(doc.page_count):
            suffix = "" if doc.page_count == 1 else f"_p{page_idx + 1}"
            out_path = output_dir / f"{pdf_path.stem}{suffix}.png"
            if out_path.exists():
                results.append(out_path)
                continue
            pix = doc.load_page(page_idx).get_pixmap(dpi=dpi)
            pix.save(str(out_path))
            results.append(out_path)
    finally:
        doc.close()
    return results


def _parse_tsv_buckets(tsv_path: Path) -> dict[str, list[str]]:
    """Bucket every TSV row by its raw column_id (header/left/middle/right/
    footer/single). `center` is normalised to `middle`. Unknown ids are
    dropped. Older TSVs without header/footer simply yield empty buckets.
    """
    buckets: dict[str, list[str]] = {
        "header": [], "left": [], "middle": [], "right": [], "footer": [],
        "single": [],
    }
    aliases = {"center": "middle"}
    with tsv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            col = row.get("column_id", "").strip()
            text = row.get("text", "").strip()
            target = col if col in buckets else aliases.get(col)
            if target:
                buckets[target].append(text)
    return buckets


def _find_stage1_file(page_dir: Path) -> Path | None:
    """Return the stage-1 prediction file for a page directory, if any.

    Prefers ``*_stage1_flat.txt`` (inference default); falls back to
    ``*_stage1.tsv`` for legacy column-mode workspaces.
    """
    flat_files = sorted(
        p for p in page_dir.glob("*_stage1_flat.txt") if not p.name.startswith("._")
    )
    if flat_files:
        return flat_files[0]
    tsv_files = sorted(
        p for p in page_dir.glob("*_stage1.tsv") if not p.name.startswith("._")
    )
    if tsv_files:
        return tsv_files[0]
    return None


def _page_stem_from_stage1(path: Path) -> str:
    stem = path.stem
    for suffix in ("_stage1_flat", "_stage1"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _build_task_data_from_flat(flat_path: Path) -> dict[str, str]:
    """Map a flat stage-1 transcript into the single-column label config."""
    text = flat_path.read_text(encoding="utf-8").strip()
    return {
        "header_text": "",
        "body_text": text,
        "footer_text": "",
    }


def _detect_layout(stage1_dir: Path) -> str:
    """Pick 'single' / 'two_col' / 'three_col' from the dominant page layout.

    Flat stage-1 files always vote ``single``. TSV pages vote from column
    buckets: middle → three_col, left/right → two_col, single → single.
    Default for empty input is two_col.
    """
    counts: Counter[str] = Counter()
    for page_dir in sorted(stage1_dir.iterdir()):
        if not page_dir.is_dir():
            continue
        pred_path = _find_stage1_file(page_dir)
        if pred_path is None:
            continue
        if pred_path.name.endswith("_stage1_flat.txt"):
            counts["single"] += 1
            continue
        b = _parse_tsv_buckets(pred_path)
        if b["middle"]:
            counts["three_col"] += 1
        elif b["left"] or b["right"]:
            counts["two_col"] += 1
        elif b["single"]:
            counts["single"] += 1
    if not counts:
        return "two_col"
    return counts.most_common(1)[0][0]


def _build_task_data(buckets: dict[str, list[str]], layout: str) -> dict[str, str]:
    """Project the raw buckets into the task-data fields the chosen layout's
    label config expects. Off-layout rows are folded somewhere reasonable so
    no body text is silently dropped:
      - layout=single:    every body bucket → body_text
      - layout=two_col:   single → left, middle → left
      - layout=three_col: single → left
    """
    header = "\n".join(buckets["header"])
    footer = "\n".join(buckets["footer"])
    if layout == "single":
        body = (buckets["single"] + buckets["left"]
                + buckets["middle"] + buckets["right"])
        return {
            "header_text": header,
            "body_text": "\n".join(body),
            "footer_text": footer,
        }
    if layout == "three_col":
        left = buckets["left"] + buckets["single"]
        return {
            "header_text": header,
            "left_text": "\n".join(left),
            "middle_text": "\n".join(buckets["middle"]),
            "right_text": "\n".join(buckets["right"]),
            "footer_text": footer,
        }
    # two_col (default)
    left = buckets["left"] + buckets["single"]
    right = buckets["right"] + buckets["middle"]
    return {
        "header_text": header,
        "left_text": "\n".join(left),
        "right_text": "\n".join(right),
        "footer_text": footer,
    }


class LabelStudioClient:
    """Thin wrapper around Label Studio HTTP API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        auth_scheme: str = "auto",
        access_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.auth_scheme = auth_scheme
        self._active_auth_scheme = "Token" if auth_scheme == "auto" else auth_scheme
        self._tried_bearer = False
        self._tried_pat_refresh = False
        self.session = requests.Session()
        if access_token:
            self._set_bearer_token(access_token)
        elif auth_scheme == "PAT":
            self._refresh_pat_access_token()
        else:
            self._set_auth_header()

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs: object) -> requests.Response:
        """Send an API request, refreshing PAT access tokens on 401 responses."""
        response = self.session.request(method, self._url(path), **kwargs)
        retry_count = 0
        while response.status_code == 401 and retry_count < 3:
            if not self._prepare_auth_retry():
                break
            retry_count += 1
            response = self.session.request(method, self._url(path), **kwargs)
        return response

    def _set_auth_header(self) -> None:
        self.session.headers.update({"Authorization": f"{self._active_auth_scheme} {self.token}"})

    def _set_bearer_token(self, token: str) -> None:
        self._active_auth_scheme = "Bearer"
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _refresh_pat_access_token(self) -> None:
        resp = self.session.post(self._url("/api/token/refresh/"), json={"refresh": self.token})
        resp.raise_for_status()
        access_token = resp.json().get("access")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("Label Studio token refresh response did not include an access token")
        self._tried_pat_refresh = True
        self._set_bearer_token(access_token)

    def _prepare_auth_retry(self) -> bool:
        if self.auth_scheme == "PAT":
            logger.info("Bearer auth failed; refreshing PAT access token")
            self._refresh_pat_access_token()
            return True
        if self.auth_scheme != "auto":
            return False
        if not self._tried_bearer:
            logger.info("Legacy token auth failed; retrying with Bearer auth")
            self._tried_bearer = True
            self._active_auth_scheme = "Bearer"
            self._set_auth_header()
            return True
        logger.info("Bearer auth failed; refreshing PAT before retrying")
        self._refresh_pat_access_token()
        return True

    def list_projects(self) -> list[dict]:
        resp = self._request("GET", "/api/projects/", params={"page_size": 1000})
        resp.raise_for_status()
        return resp.json().get("results", [])

    def delete_project(self, project_id: int) -> bool:
        resp = self._request("DELETE", f"/api/projects/{project_id}/")
        if resp.ok:
            return True
        logger.warning("Failed to delete project %d: %s", project_id, resp.text[:200])
        return False

    def create_project(self, title: str, label_config: str, description: str = "") -> dict | None:
        resp = self._request("POST", "/api/projects/", json={
            "title": title,
            "description": description,
            "label_config": label_config,
            "is_published": True,
            "show_skip_button": True,
            "enable_empty_annotation": True,
        })
        if not resp.ok:
            logger.error("Failed to create project '%s': %s", title, resp.text[:300])
            return None
        return resp.json()

    def import_tasks(self, project_id: int, tasks: list[dict]) -> dict:
        resp = self._request(
            "POST",
            f"/api/projects/{project_id}/import",
            json=tasks,
        )
        resp.raise_for_status()
        return resp.json()

    def create_local_storage(self, project_id: int, storage_path: str) -> dict | None:
        """Create a local file import-storage connection for the project.

        ``storage_path`` must be a strict subdirectory of
        ``LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT`` (Label Studio rejects paths
        that are equal to the document root).
        """
        resp = self._request(
            "POST",
            "/api/storages/localfiles",
            json={
                "path": storage_path,
                "project": project_id,
                "use_blob_urls": True,
                "title": "Page Renders",
            },
        )
        if resp.ok:
            return resp.json()
        logger.error("Failed to create local storage: %s", resp.text[:1000])
        return None

    def upload_file(self, project_id: int, file_path: Path) -> str:
        """Upload a file and return its serving URL."""
        with file_path.open("rb") as f:
            resp = self._request(
                "POST",
                f"/api/projects/{project_id}/import",
                files={"file": (file_path.name, f, "image/png")},
            )
        resp.raise_for_status()
        return resp.json()


def _entry_storage_path(
    storage_root: str | None, render_dir: Path, entry_name: str
) -> str:
    """Server-side Local Files storage path for one dictionary entry.

    Label Studio requires this to be a subdirectory of
    ``LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT`` (typically the parent of
    per-language render folders).
    """
    entry_render = render_dir / entry_name
    if storage_root:
        return f"{storage_root.rstrip('/')}/{entry_name}"
    return str(entry_render.resolve())


def setup_project_for_entry(
    client: LabelStudioClient,
    entry_dir: Path,
    render_dir: Path,
    *,
    storage_root: str | None = None,
    connect_local_storage: bool = False,
    overwrite: bool = False,
) -> int | None:
    """Create a Label Studio project for one dictionary entry.

    Returns the project ID on success, None if skipped.

    Args:
        client: Label Studio API client.
        entry_dir: Root directory for this language (contains snippets/ and outputs/).
        render_dir: Local directory where PNGs are rendered to.
        storage_root: Parent of per-language render dirs on the Label Studio
            host (``LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT``). Each project
            registers ``<storage_root>/<entry_name>`` as import storage.
            Only used when connect_local_storage is True.
        connect_local_storage: If True, register a per-project Local Files
            import storage (required for /data/local-files/?d=... image URLs
            on a remote Label Studio instance).
        overwrite: If True, recreate the project even if it already exists.
    """
    entry_name = entry_dir.name
    stage1_dir = entry_dir / "outputs" / "stage-1"
    snippets_dir = entry_dir / "snippets"

    if not stage1_dir.is_dir():
        logger.warning("Skipping %s: no outputs/stage-1/ folder", entry_name)
        return None
    if not snippets_dir.is_dir():
        logger.warning("Skipping %s: no snippets/ folder", entry_name)
        return None

    # Group by title because Label Studio allows duplicate project names —
    # past runs without proper overwrite handling may have created several.
    existing: dict[str, list[int]] = {}
    for p in client.list_projects():
        existing.setdefault(p["title"], []).append(p["id"])

    project_title = f"Post-Edit: {entry_name}"
    if len(project_title) > 50:
        project_title = entry_name[:50]

    matching_ids = existing.get(project_title, [])
    if matching_ids and not overwrite:
        logger.info(
            "Project '%s' already exists (id=%d), skipping",
            project_title,
            matching_ids[0],
        )
        return matching_ids[0]

    if matching_ids and overwrite:
        for pid in matching_ids:
            if client.delete_project(pid):
                logger.info("Deleted existing project '%s' (id=%d)", project_title, pid)

    layout = _detect_layout(stage1_dir)
    logger.info("Detected layout '%s' for %s", layout, entry_name)

    project = client.create_project(
        title=project_title,
        label_config=_build_label_config(layout),
        description=f"Post-editing OCR transcription for {entry_name} dictionary pages.",
    )
    if project is None:
        return None
    project_id = project["id"]
    logger.info("Created project '%s' (id=%d)", project_title, project_id)

    entry_render_dir = render_dir / entry_name
    entry_render_dir.mkdir(parents=True, exist_ok=True)

    if connect_local_storage:
        storage_path = _entry_storage_path(storage_root, render_dir, entry_name)
        storage = client.create_local_storage(project_id, storage_path)
        if storage:
            logger.info("  Local storage connected: %s (id=%s)", storage_path, storage.get("id"))
        else:
            logger.error(
                "  Local storage not connected — images will not load. "
                "Add Local Files source in project settings with path: %s",
                storage_path,
            )
    tasks: list[dict] = []

    page_dirs = sorted(d for d in stage1_dir.iterdir() if d.is_dir())
    for page_dir in page_dirs:
        pred_path = _find_stage1_file(page_dir)
        if pred_path is None:
            continue

        page_stem = _page_stem_from_stage1(pred_path)

        pdf_path = snippets_dir / f"{page_stem}.pdf"
        if not pdf_path.exists():
            img_candidates = list(snippets_dir.glob(f"{page_stem}.*"))
            if img_candidates:
                pdf_path = img_candidates[0]
            else:
                logger.warning("  No snippet found for %s", page_stem)
                continue

        if pdf_path.suffix.lower() == ".pdf":
            rendered = _render_pdf_to_png(pdf_path, entry_render_dir)
            if not rendered:
                logger.warning("  Failed to render %s", pdf_path.name)
                continue
            image_path = rendered[0]
        else:
            dest = entry_render_dir / pdf_path.name
            if not dest.exists():
                shutil.copy2(pdf_path, dest)
            image_path = dest

        if pred_path.name.endswith("_stage1_flat.txt"):
            text_fields = _build_task_data_from_flat(pred_path)
        else:
            buckets = _parse_tsv_buckets(pred_path)
            text_fields = _build_task_data(buckets, layout)

        # ?d= is relative to LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT (the parent
        # of per-language render dirs). Import storage uses the subdir only.
        task = {
            "data": {
                "image_url": f"/data/local-files/?d={entry_name}/{image_path.name}",
                **text_fields,
                "page_name": page_stem,
                "language": entry_name,
            },
        }
        tasks.append(task)
        logger.info("  Prepared task for %s", page_stem)

    if not tasks:
        logger.warning("No tasks to import for %s", entry_name)
        return project_id

    result = client.import_tasks(project_id, tasks)
    task_count = result.get("task_count", len(tasks))
    logger.info("Imported %d tasks into project '%s'", task_count, project_title)
    return project_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up Label Studio projects for OCR post-editing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--samples-dir",
        type=Path,
        default=Path("assets/dictionaries/samples-2"),
        help="Root directory with language subfolders",
    )
    parser.add_argument(
        "--languages", nargs="+", default=None,
        help="Only process these language subfolders",
    )
    parser.add_argument(
        "--ls-url",
        default=os.getenv("LABEL_STUDIO_URL", "http://localhost:8080"),
        help="Label Studio base URL",
    )
    parser.add_argument(
        "--ls-token",
        default=os.getenv("LABEL_STUDIO_TOKEN"),
        help="Label Studio API token (legacy or PAT)",
    )
    parser.add_argument(
        "--ls-access-token",
        default=os.getenv("LABEL_STUDIO_ACCESS_TOKEN"),
        help="Optional short-lived Label Studio access token for Bearer auth.",
    )
    parser.add_argument(
        "--ls-auth-scheme",
        choices=sorted(AUTH_SCHEMES),
        default=os.getenv("LABEL_STUDIO_AUTH_SCHEME", "auto"),
        help="Authorization scheme: auto, Token, Bearer, or PAT (refresh token -> access token).",
    )
    parser.add_argument(
        "--render-dir",
        type=Path,
        default=Path("label-studio/renders"),
        help="Local directory for rendered PNG page images",
    )
    parser.add_argument(
        "--storage-root",
        type=str,
        default=None,
        help=(
            "LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT on the Label Studio host "
            "(parent of per-language render subdirs). Each project registers "
            "<storage_root>/<entry_name> as Local Files storage."
        ),
    )
    parser.add_argument(
        "--connect-local-storage",
        action="store_true",
        help=(
            "Register Local Files import storage on each project (required for "
            "/data/local-files/?d=... image URLs on remote Label Studio). "
            "Implied when --storage-root is set."
        ),
    )
    parser.add_argument("--overwrite", action="store_true", help="Recreate existing projects")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.ls_token:
        logger.error("No Label Studio token provided. Use --ls-token or set LABEL_STUDIO_TOKEN.")
        return 1

    if not args.samples_dir.is_dir():
        logger.error("Samples directory not found: %s", args.samples_dir)
        return 1

    try:
        client = LabelStudioClient(
            args.ls_url,
            args.ls_token,
            args.ls_auth_scheme,
            access_token=args.ls_access_token,
        )
        projects = client.list_projects()
        logger.info("Connected to Label Studio (%d existing projects)", len(projects))
    except (requests.HTTPError, RuntimeError) as e:
        logger.error("Failed to connect to Label Studio: %s", e)
        return 1

    # Create in reverse-alphabetical order so Label Studio's default
    # newest-first dashboard sort renders them A→Z.
    entries = sorted(
        (p for p in args.samples_dir.iterdir() if p.is_dir()),
        key=lambda p: p.name,
        reverse=True,
    )
    if args.languages:
        requested = set(args.languages)
        entries = [e for e in entries if e.name in requested]

    logger.info("Processing %d dictionary entries", len(entries))

    connect_storage = args.connect_local_storage or bool(args.storage_root)

    created = 0
    for entry_dir in entries:
        project_id = setup_project_for_entry(
            client,
            entry_dir,
            args.render_dir,
            storage_root=args.storage_root,
            connect_local_storage=connect_storage,
            overwrite=args.overwrite,
        )
        if project_id is not None:
            created += 1

    logger.info("Done. %d projects set up.", created)
    return 0


if __name__ == "__main__":
    sys.exit(main())

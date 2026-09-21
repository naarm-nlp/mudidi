# Dataset Card Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize the MUDIDI Hugging Face card and Parquet exports with the current 30-dictionary, 90-page gold release and make the release instructions reproducible.

**Architecture:** The current raw gold tree under `dataset/MUDIDI/dictionaries` is the source of truth. A self-contained Parquet builder discovers only dictionary folders containing `dictionary_languages.yaml`, derives the Hugging Face config slug from each folder name, and writes the fixed three-column schema. The card documents the resulting release; the existing projection generator emits portable paths and current hashes.

**Tech Stack:** Python 3.11, `pyarrow`, `datasets`, Pydantic/YAML data files, Hugging Face Hub CLI/API, MkDocs, pytest.

**Spec:** Approved dataset audit findings and implementation design in the preceding conversation; no separate product spec.

## Global Constraints

- Preserve the 30 canonical remote dictionaries and exclude local-only directories that do not contain `dictionary_languages.yaml`.
- Treat the current raw Stage 1 TSV/flat/language-map files as authoritative; do not alter raw gold text or source images.
- Every generated Parquet row must have exactly `page_id`, `ocr_text`, and `mdf_text` string columns.
- The corrected release must contain 90 Stage 1 rows, three per canonical dictionary, and ten non-empty Stage 2 MDF rows.
- Do not touch the unrelated dirty files already present in the checkout.
- Use relative dataset-root paths in public projection JSON and recompute all referenced SHA-256 values.
- Do not publish to Hugging Face until local generated artifacts and metadata pass the complete verification command.

---

### Task 1: Make the Parquet builder self-contained and testable

**Files:**
- Create: `scripts/build_dataset_parquet.py`
- Modify: `pyproject.toml:27-34, optional dependency groups`
- Modify: `uv.lock` through `uv lock`
- Create: `tests/dataset/test_build_parquet.py`

**Interfaces:**
- Produces `_config_name(dictionary_id: str) -> str`, `_dictionary_configs() -> dict[str, Path]`, `_rows_for_dictionary(dictionary_dir: Path) -> list[dict[str, str]]`, and `build_all() -> dict[str, int]` in the dataset builder.
- `_dictionary_configs()` discovers directories with `dictionary_languages.yaml`, maps normalized names to lowercase hyphenated config names, and rejects duplicate slugs.
- `build_all()` writes `parquet/<config>/train.parquet` relative to the builder root and returns config-to-row-count mappings.

- [ ] **Step 1: Write failing tests**

Add tests covering:

```python
def test_config_name_removes_diacritics_and_normalizes_separators() -> None:
    assert _config_name("Iñupiatun Eskimo-English") == "inupiatun-eskimo-english"
    assert _config_name("Vernacular Syriac-Kurdish_Turkish-English") == (
        "vernacular-syriac-kurdish-turkish-english"
    )


def test_dictionary_configs_excludes_directories_without_language_metadata(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "Canonical-English"
    canonical.mkdir()
    (canonical / "dictionary_languages.yaml").write_text("source: x\n", encoding="utf-8")
    (tmp_path / "Local-only-English").mkdir()
    monkeypatch.setattr(build_parquet, "DICTIONARIES_DIR", tmp_path)
    assert list(build_parquet._dictionary_configs()) == ["canonical-english"]


def test_build_all_writes_current_schema_and_mdf_value(tmp_path, monkeypatch) -> None:
    dictionary = tmp_path / "Greek-English"
    page = dictionary / "Stage 1 Gold OCR" / "page_38"
    page.mkdir(parents=True)
    (dictionary / "dictionary_languages.yaml").write_text("source: grc\n", encoding="utf-8")
    (page / "page_38_stage1_GOLD_flat.txt").write_text("\u03bb\u03bf\u03b3\u03bf\u03c2\n", encoding="utf-8")
    mdf = dictionary / "Stage 2 MDF file" / "page_38"
    mdf.mkdir(parents=True)
    (mdf / "page_38.mdf.txt").write_text("\\lx \u03bb\u03bf\u03b3\u03bf\u03c2\n", encoding="utf-8")
    monkeypatch.setattr(build_parquet, "DICTIONARIES_DIR", tmp_path)
    monkeypatch.setattr(build_parquet, "PARQUET_DIR", tmp_path / "parquet")
    assert build_parquet.build_all() == {"greek-english": 1}
    table = pq.read_table(tmp_path / "parquet/greek-english/train.parquet")
    assert table.column_names == ["page_id", "ocr_text", "mdf_text"]
    assert table.to_pylist() == [{"page_id": "page_38", "ocr_text": "\u03bb\u03bf\u03b3\u03bf\u03c2\n", "mdf_text": "\\lx \u03bb\u03bf\u03b3\u03bf\u03c2\n"}]
```

Use real temporary files and PyArrow; do not mock Parquet behavior.

- [ ] **Step 2: Run the focused tests and verify the expected import failure**

Run:

```bash
uv run --with pyarrow pytest tests/dataset/test_build_parquet.py -q
```

Expected before implementation: collection fails because the existing builder imports missing `mudidi.DICTIONARY_CONFIGS`/`_FLAT_SUFFIX`.

- [ ] **Step 3: Implement the minimal builder change**

Create the tracked `scripts/build_dataset_parquet.py`. Its default root is `dataset/MUDIDI` when run from the source repository, or the script's own parent when uploaded as `build_parquet.py` at the HF dataset root. Remove the package import. Define the fixed suffix, Unicode slug normalization, metadata-backed directory discovery, duplicate-slug validation, and Path-based row collection. Keep the existing Parquet schema and output locations. Add `pyarrow>=18.0` to the `dev` extra and add a `dataset` extra containing `datasets>=3.0` and `pyarrow>=18.0`.

- [ ] **Step 4: Update the lockfile and run the focused tests**

Run:

```bash
uv lock
uv run --extra dev pytest tests/dataset/test_build_parquet.py -q
```

Expected: all focused builder tests pass.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add scripts/build_dataset_parquet.py pyproject.toml uv.lock tests/dataset/test_build_parquet.py
git commit -m "fix: make dataset parquet builder self-contained"
```

---

### Task 2: Regenerate Parquet exports and synchronize card metadata

**Files:**
- Modify generated: `dataset/MUDIDI/parquet/*/train.parquet`
- Modify: `dataset/MUDIDI/README.md` frontmatter `dataset_info`

**Interfaces:**
- Consumes the Task 1 builder and the current canonical raw gold tree.
- Produces 30 Parquet files with three rows each and frontmatter reporting `num_examples: 3` for every configuration.

- [ ] **Step 1: Run the builder against the canonical local dataset**

Run:

```bash
uv run --extra dataset python scripts/build_dataset_parquet.py
```

Expected: 30 config lines, each reporting 3 rows, with no local-only config output.

- [ ] **Step 2: Update all 30 frontmatter row counts**

Change the three stale `dataset_info` entries for Georgian-Russian, Japanese-English, and Yiddish-English from 1/2/1 to 3. Confirm every config has exactly one `train` split and `num_examples: 3`.

- [ ] **Step 3: Verify every local Parquet export**

Run a `datasets` smoke check over all 30 configs and assert exact schema, three rows per config, page IDs matching the current Stage 1 flat files, and exactly ten non-empty `mdf_text` rows. Fail on any missing file, extra config, schema mismatch, or payload mismatch.

- [ ] **Step 4: Keep generated HF artifacts out of the source checkout commit**

The dataset directory is intentionally ignored by the source repository. Preserve the regenerated Parquet and card locally for the publication step; do not force-add the 30 Parquet files or HF README to the GitHub source repository. The tracked builder source is `scripts/build_dataset_parquet.py`; the publication step uploads it as the dataset-root `build_parquet.py`.

Do not stage unrelated dirty files.

---

### Task 3: Correct dataset card, license, and public documentation

**Files:**
- Modify: `dataset/MUDIDI/README.md:489-662`
- Modify: `dataset/MUDIDI/LICENSE:3-10`
- Create: `docs/benchmarking/dataset.md`
- Modify: `docs/benchmarking/index.md` with a dataset-format link
- Modify: `mkdocs.yml` with a Dataset format navigation entry
- Modify: `docs/CODEMAPS/data.md:10-26, 98-108`

**Interfaces:**
- The HF README remains self-contained for users who only read the card.
- The new public documentation page is the valid target for Stage 1 and Stage 2 methodology links.

- [ ] **Step 1: Add the documentation page with the actual release contract**

Document the 30 canonical dictionaries, 90 source pages, 90 Stage 1 TSV/flat/lang-map triplets, ten Stage 2 guide/MDF/projection sets, the Circassian flattened-column nuance, Parquet schema, source-of-truth policy, and reproducible builder command.

- [ ] **Step 2: Update card summary and layout**

Change the summary to 30 multilingual dictionaries, 90 Stage 1 gold pages, three pages per dictionary, ten Stage 2 pages, and one `train` split per config with no train/test partition. Add `parquet/` and `build_parquet.py` to the top-level layout. Document `*_lang.json` and `*_mdf_lang_projection.json`.

- [ ] **Step 3: Correct format and limitation wording**

Describe alphabet files as dictionary-specific inventories that may contain case pairs, digraphs, headings, or prose. State that no `Introduction/` folders are included. Explain that Stage 2 covers ten dictionaries and that all 90 current Stage 1 pages have gold artifacts. Qualify the Circassian TSV/flat representation.

- [ ] **Step 4: Repair commands and links**

Use `dataset/MUDIDI` consistently in local download examples. Replace the broken methodology URLs with the valid dataset documentation page. Replace the false GitHub-vendored-data claim with an explicit statement that data is published on Hugging Face and not tracked in the source checkout. Document the source-repository command:

```bash
uv run --extra dataset python scripts/build_dataset_parquet.py
```

For a downloaded HF dataset root containing `build_parquet.py`, document `uv run --extra dataset python build_parquet.py`.

- [ ] **Step 5: Clarify licensing and update the project data map**

Mention language maps and projections as annotations under CC BY-NC 4.0; describe source files as public-domain-derived PDFs and PNGs. Update `docs/CODEMAPS/data.md` to use the actual filenames and canonical counts.

- [ ] **Step 6: Build documentation and inspect changed links**

Run:

```bash
uv run --extra docs mkdocs build --strict
```

Expected: documentation builds with no broken internal links.

- [ ] **Step 7: Commit documentation changes**

```bash
git add docs/benchmarking/dataset.md docs/benchmarking/index.md mkdocs.yml docs/CODEMAPS/data.md
git commit -m "docs: reconcile dataset release documentation"
```

The HF README and LICENSE remain ignored local release files and are uploaded in Task 5; do not force-add them to the source repository.

---

### Task 4: Make Stage 2 projections portable and clean release metadata

**Files:**
- Modify: `src/mudidi/evaluation/stage2/mdf_stage1_projection.py:489-535`
- Modify: `tests/evaluation/stage2/test_mdf_stage1_projection.py` (create if absent)
- Modify generated: `dataset/MUDIDI/dictionaries/*/Stage 2 MDF file/page_*/page_*_mdf_lang_projection.json`
- Modify: `dataset/MUDIDI/.gitattributes`
- Delete generated: `dataset/MUDIDI/.DS_Store` if present

**Interfaces:**
- `write_dataset_json(dataset_dir, ...)` must preserve the existing projection schema while writing paths relative to `dataset_dir` using POSIX separators.
- SHA fields must continue to hash the exact referenced local files.

- [ ] **Step 1: Write a failing projection portability test**

Create a temporary dataset with one Stage 1 flat/lang-map pair and one MDF file, call `write_dataset_json` with an absolute dataset path, and assert that generated `gold_mdf_path`, `stage1_gold_path`, and `stage1_lang_map_path` begin with `dictionaries/` rather than an author-local absolute path.

- [ ] **Step 2: Run the focused projection test and verify it fails**

Run:

```bash
uv run pytest tests/evaluation/stage2/test_mdf_stage1_projection.py -q
```

Expected before implementation: generated paths are absolute.

- [ ] **Step 3: Implement relative path emission**

Use `Path.relative_to(dataset_dir).as_posix()` for the three public path fields. Leave projection values, field ordering, and version unchanged.

- [ ] **Step 4: Regenerate all ten projections**

Run the existing projection writer over `dataset/MUDIDI/dictionaries` with `--write-dataset-json`, then assert every projection has current hashes and no absolute path prefix.

- [ ] **Step 5: Remove stale Coptic attributes and tracked OS metadata**

Delete only the two Coptic lines from `.gitattributes` and remove `.DS_Store`; retain canonical source-page LFS patterns.

- [ ] **Step 6: Run focused tests and commit**

```bash
uv run pytest tests/evaluation/stage2/test_mdf_stage1_projection.py tests/schemas/test_mdf_parsing_guide_contract.py -q
git add src/mudidi/evaluation/stage2/mdf_stage1_projection.py tests/evaluation/stage2/test_mdf_stage1_projection.py dataset/MUDIDI/.gitattributes
git commit -m "fix: make dataset projections portable"
```

---

### Task 5: Publish and perform end-to-end release verification

**Files:**
- Publish: `README.md`, `LICENSE`, `build_parquet.py`, `.gitattributes`, `parquet/**`, ten projection JSON files to `Davidsamuel101/MUDIDI`

**Interfaces:**
- The HF dataset must retain exactly 30 canonical dictionary roots and 30 Parquet config directories.
- The published card, Parquet rows, raw gold artifacts, and projections must agree.

- [ ] **Step 1: Run the complete local verification**

Run:

```bash
uv run --extra dev pytest
uv run --extra docs mkdocs build --strict
uv run --extra dataset python dataset/MUDIDI/build_parquet.py
```

Then run the release audit asserting 30 configs, 90 Parquet rows, 90 Stage 1 triplets, ten Stage 2 sets, valid projection hashes, no absolute projection paths, no Introduction folders, and no local-only dictionary uploads.

- [ ] **Step 2: Upload only the verified release files**

Use authenticated Hugging Face upload operations for the corrected README, LICENSE, builder, `.gitattributes`, all regenerated Parquet files, and ten regenerated projection JSON files. Do not upload the seven local-only dictionary directories.

- [ ] **Step 3: Re-fetch the live dataset and verify the published state**

Run the all-config `datasets` load and tree/hash audit against the live dataset. Confirm 30 configurations, 90 total rows, three rows per config, current OCR/MDF payloads, valid card metadata, and no stale absolute paths.

- [ ] **Step 4: Commit any final tracked verification-only fixes**

```bash
git status --short
```

Stage only files changed by this plan; leave the pre-existing unrelated dirty files untouched.

---

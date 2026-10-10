# Prompt templates

MUDIDI's canonical prompt templates are stored under
`src/mudidi/assets/prompts/`. The directory's `manifest.json` is an index of
prompt IDs, files, and variable metadata; it is not the code that assembles an
LLM request.

Each request contains separate system and user messages. The user template is
rendered first, then page images or PDF attachments are added as multimodal
content parts by the pipeline.

## Stage 1

| Mode | System template | User template |
| --- | --- | --- |
| Flat benchmark | `stage_1/system_benchmark.txt` | `stage_1/user_benchmark.j2` |
| Flat inference | `stage_1/system_inference.j2` | `stage_1/user_inference.j2` |
| Legacy column mode | `stage_1/legacy/column_system.j2` | `stage_1/user_benchmark.j2` |

The benchmark user template preserves the historical experiment contract. It
contains optional alphabet, OCR-reference, and user-guide sections. The
inference user template additionally contains the optional Dictionary Profile
block. Typography is always requested by the benchmark system prompt; it is an
inference option in the inference and legacy-column system templates.

## Stage 2 Pass 1

Pass 1 pairs `stage_2/pass_1/system.j2` with either
`user_single.j2` or `user_multi.j2`. The optional `config_hint` block is
populated from benchmark `dictionary_languages.yaml` metadata or an inference
Dictionary Profile. Introduction pages and sample dictionary pages follow the
text as image or PDF content parts, in that order.

`mdf_marker_reference.txt` is injected into the Pass 1 system message. When the
MDF manual flag is on (`input.mdf_manual: true` or `--mdf-manual`), the bundled
`mdf_reference_manual.txt` excerpt of the SIL Toolbox Reference Manual is
appended to that system message as well.

## Stage 2 Pass 2

| Mode | System template | User template |
| --- | --- | --- |
| Benchmark | `stage_2/pass_2/system_benchmark.txt` | `stage_2/pass_2/user_benchmark.j2` |
| Inference | `stage_2/pass_2/system_inference.j2` | `stage_2/pass_2/user_inference.j2` |

Optional values are visible as Jinja conditionals in the user templates.
User guides are included only when supplied. In inference the user message
also carries the end of the previous page's Stage 1 transcript, as text, so
lines that continue an earlier entry get the right markers; the next page is
not sent, and the only page image is the current page. Pass 2 never receives
the SIL MDF manual.

The per-folder README files under `src/mudidi/assets/prompts/stage_1/` and
`stage_2/pass_1/` and `stage_2/pass_2/` document the exact message pairings and
attachment behavior.

# Stage 2, Pass 2: MDF extraction

Every extraction request has a system message and a user message, plus the
current dictionary-page image. In inference, neighboring pages are supplied as
transcript excerpts inside the user message (the end of the previous page and
the start of the next), never as images.

| Mode | System message | User message |
| --- | --- | --- |
| Benchmark | `system_benchmark.txt` | `user_benchmark.j2` |
| Inference | `system_inference.j2` | `user_inference.j2` |

`page_boundary_rules.txt` is injected into the inference system message.
Pass 2 does not receive the SIL MDF manual; the optional bundled manual text
informs only Pass 1 parsing-guide discovery.

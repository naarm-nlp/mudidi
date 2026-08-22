# Stage 2, Pass 2: MDF extraction

Every extraction request has a system message and a user message, plus the
current dictionary-page image. In inference, neighboring page images may be
attached after the user text.

| Mode | System message | User message |
| --- | --- | --- |
| Benchmark | `system_benchmark.txt` | `user_benchmark.j2` |
| Inference | `system_inference.j2` | `user_inference.j2` |

`page_boundary_rules.txt` is injected into the inference system message.
The user templates visibly select `toolbox_reference_mode`: `pdf` labels an
attached Toolbox manual PDF, `text_fallback` embeds the Pass 1 MDF reference,
and `none` omits the manual. This keeps the fallback behavior clear without a
disconnected `toolbox_text_section` prompt.

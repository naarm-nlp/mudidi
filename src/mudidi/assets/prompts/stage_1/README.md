# Stage 1: OCR transcription

Every Stage 1 request contains two separate model messages: a system template
and `user.j2`, followed by the dictionary-page image. An alphabet image may
also be attached when an alphabet reference is supplied.

| Mode | System message | User message |
| --- | --- | --- |
| Flat benchmark | `system_benchmark.txt` | `user.j2` |
| Flat inference | `system_inference.j2` | `user.j2` |
| Legacy column mode | `legacy/column_system.j2` | `user.j2` |

`user.j2` makes the optional `alphabet_text`, `ocr_hint`,
`dictionary_profile_context`, and `guides` sections explicit. The benchmark
system prompt always requests typography tags; inference and legacy column
mode use the visible `typography` conditional when that option is enabled.

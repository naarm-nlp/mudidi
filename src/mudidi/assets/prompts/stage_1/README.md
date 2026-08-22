# Stage 1: OCR transcription

Every Stage 1 request contains two separate model messages: a system template
and a mode-specific user template, followed by the dictionary-page image. An alphabet image may
also be attached when an alphabet reference is supplied.

| Mode | System message | User message |
| --- | --- | --- |
| Flat benchmark | `system_benchmark.txt` | `user_benchmark.j2` |
| Flat inference | `system_inference.j2` | `user_inference.j2` |
| Legacy column mode | `legacy/column_system.j2` | `user_benchmark.j2` |

`user_benchmark.j2` contains only the alphabet, OCR-reference, and guides
conditionals that existed in the original benchmark pipeline.
`user_inference.j2` adds the optional `dictionary_profile` block, whose fixed
instructions are intentionally visible in that template. The benchmark system
prompt always requests typography tags; inference and legacy column mode use
the visible `typography` conditional when that option is enabled.

# Stage 2, Pass 2: MDF extraction

Every extraction request has a system message and a user message, plus the
current dictionary-page image. Inference parses each page locally: the MDF
covers exactly the lines printed on that page, and per-page outputs are
concatenated in page order to form the dictionary. The end of the previous
page's transcript is supplied as text inside the user message, only so that
lines continuing an earlier entry get the right markers. The next page is not
sent, and neighboring pages are never sent as images.

| Mode | System message | User message |
| --- | --- | --- |
| Benchmark | `system_benchmark.txt` | `user_benchmark.j2` |
| Inference | `system_inference.j2` | `user_inference.j2` |

`page_boundary_rules.txt` is injected into the inference system message.
Pass 2 does not receive the SIL MDF manual; the optional bundled manual text
informs only Pass 1 parsing-guide discovery.

# MUDIDI prompt templates

`manifest.json` is an index of prompt IDs, source files, and the values each
template receives. It does not assemble a model request. The files under
`stage_1/` and `stage_2/` are the canonical, readable message templates.

Files ending in `.txt` are static. Files ending in `.j2` are Jinja templates;
their visible `{% if ... %}` blocks describe optional context exactly where it
appears in the final message.

Each Stage README records which system and user messages form a request and
which images or document attachments accompany them.

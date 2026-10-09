# Stage 2, Pass 1: MDF field discovery

Each request combines `system.j2` as the system message with exactly one user
message: `user_single.j2` for one sample page or `user_multi.j2` for multiple
sample pages. The introduction and sample-page image/PDF attachments are added
after the text content in the same request.

`mdf_marker_reference.txt` is always injected into `system.j2`. When the MDF
manual flag is on (`input.mdf_manual: true`, `--mdf-manual`, or the dashboard's
"Include the SIL MDF manual" choice), `mdf_reference_manual.txt` — text
extracted from the MDF section of the SIL Toolbox Reference Manual, ending
before "Other General Information" — is appended to the same system message.

The `config_hint` section in both user templates is conditional: it appears
only when benchmark `dictionary_languages.yaml` metadata or an inference
Dictionary Profile supplies language-role and layout context.

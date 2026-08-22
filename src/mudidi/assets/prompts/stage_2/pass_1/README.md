# Stage 2, Pass 1: MDF field discovery

Each request combines `system.j2` as the system message with exactly one user
message: `user_single.j2` for one sample page or `user_multi.j2` for multiple
sample pages. The introduction and sample-page images are attached after the
text content in the same request.

`mdf_marker_reference.txt` is injected into `system.j2`. It is owned here
because Pass 1 is its primary use; Pass 2 reuses it only as a PDF fallback.

The `config_hint` section in both user templates is conditional: it appears
only when benchmark `dictionary_languages.yaml` metadata or an inference
Dictionary Profile supplies language-role and layout context.

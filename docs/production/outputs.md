# Output layout

```text
output/
├── resolved_config.json
├── parse-rules.json
├── run_usage.json
├── stage-1/page_N/
│   ├── page_N_stage1_flat.txt
│   └── page_N_usage.json
└── stage-2/page_N/
    ├── page_N.mdf.txt
    └── page_N_usage.json
```

Existing stage-level `run_config.json` manifests retain their resume semantics. `resolved_config.json` records the redacted configuration used to start the invocation.

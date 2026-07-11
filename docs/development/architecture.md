# Architecture

The public boundary is a command-specific Pydantic configuration. CLI values and YAML resolve into that boundary before execution. A single compatibility adapter feeds the existing extraction orchestration while it is gradually made configuration-native.

See the token-lean codemaps under `docs/CODEMAPS/` for subsystem details.

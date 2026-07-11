#!/usr/bin/env bash
# Directory mode on Evenki-Russian from the MUDIDI benchmark dataset.
# Requires dataset/mudidi/ (download from Hugging Face — see examples/README.md).
#
# Usage (from repo root): bash examples/inference/run_directory_mode.sh

uv run mudidi run \
  --config examples/configs/production/directory-inference.yaml \
  "$@"

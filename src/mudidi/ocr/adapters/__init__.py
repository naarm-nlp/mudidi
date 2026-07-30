"""OCR layout adapters → flat stage-1 transcript (spec v2)."""

from mudidi.ocr.adapters.flat_export import write_stage1_flat_for_page
from mudidi.ocr.adapters.layout_to_transcript_v1 import (
    FlatTranscriptParts,
    layout_to_transcript_v1,
)

__all__ = [
    "FlatTranscriptParts",
    "layout_to_transcript_v1",
    "write_stage1_flat_for_page",
]

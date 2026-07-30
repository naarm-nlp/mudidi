"""
Unified OCR output types produced by all OCR backends.
Every backend returns an OCRPageResult; downstream modules only depend on this schema.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
import numpy as np


@dataclass
class BBox:
    """Axis-aligned bounding box."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    @property
    def cx(self) -> float:
        return (self.x_min + self.x_max) / 2

    @property
    def cy(self) -> float:
        return (self.y_min + self.y_max) / 2

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    def to_dict(self) -> Dict:
        return {
            "x_min": float(self.x_min),
            "y_min": float(self.y_min),
            "x_max": float(self.x_max),
            "y_max": float(self.y_max),
        }


@dataclass
class OCRLine:
    """A single detected text line with its bounding box and confidence."""

    bbox: BBox
    text: str
    confidence: float
    column: Optional[str] = None  # 'L' or 'R', assigned after column segmentation

    def to_dict(self) -> Dict:
        return {
            "bbox": self.bbox.to_dict(),
            "text": self.text,
            "confidence": float(self.confidence),
            "column": self.column,
        }


@dataclass
class OCRBlock:
    """
    A logical block of text (e.g. a paragraph or column detected by a layout model).
    Groups multiple lines and may carry a semantic label from the layout analysis.
    """

    block_id: int
    bbox: BBox
    lines: List[OCRLine] = field(default_factory=list)
    label: str = "text"            # e.g. 'text', 'paragraph_title', 'table', etc.
    content: str = ""              # Full text content of the block (if provided by backend)
    crop_image: Optional[np.ndarray] = None  # Cropped image of the block, if generated

    def to_dict(self) -> Dict:
        return {
            "block_id": self.block_id,
            "bbox": self.bbox.to_dict(),
            "lines": [line.to_dict() for line in self.lines],
            "label": self.label,
            "content": self.content,
        }


@dataclass
class OCRPageResult:
    """
    The unified output produced by every OCR backend.
    Downstream extraction strategies only consume this type.
    """

    source_image: str          # Path to the original image that was processed
    backend: str               # Name of the OCR backend that produced this result
    blocks: List[OCRBlock] = field(default_factory=list)
    raw_text: str = ""         # Full concatenated text (for backends that return plain text)

    @property
    def text_blocks(self) -> List[OCRBlock]:
        """Returns only blocks labelled as 'text'."""
        return [b for b in self.blocks if b.label == "text"]

    @property
    def all_lines(self) -> List[OCRLine]:
        """Flat list of all OCR lines across all blocks."""
        return [line for block in self.blocks for line in block.lines]

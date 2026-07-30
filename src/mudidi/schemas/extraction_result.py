"""Result model for direct-MDF extraction."""

from pydantic import BaseModel


class ExtractionResult(BaseModel):
    """MDF text and page provenance returned by an extraction strategy."""

    page_number: int
    source_file: str
    mdf_text: str = ""

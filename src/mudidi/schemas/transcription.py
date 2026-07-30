"""Pydantic schemas for Stage 1 transcription."""

from pydantic import BaseModel, Field


class ColumnTranscription(BaseModel):
    """Lines from a single detected column, read top-to-bottom."""

    column_id: str = Field(
        description=(
            "Column identifier. Use 'left', 'center', 'right' for multi-column pages, "
            "or 'single' when the page has only one column."
        )
    )
    lines: list[str] = Field(
        description=(
            "Every visible line of text in this column exactly as it appears, "
            "one string per line, top to bottom. Preserve all diacritics, stress marks, "
            "and special characters. Do not merge, skip, or paraphrase any line. "
            "Wrap bold text in <b>...</b> and italic text in <i>...</i> tags."
        )
    )


class FlatTranscriptionResponse(BaseModel):
    """Structured output for flat Stage 1 transcription."""

    header: list[str] = Field(default_factory=list)
    lines: list[str] = Field(
        description=(
            "Every visible body line in reading order. Wrap bold in <b>...</b> and "
            "italic in <i>...</i>."
        )
    )
    footer: list[str] = Field(default_factory=list)


class FlatTranscriptionResponsePlain(BaseModel):
    """Structured output for flat Stage 1 transcription without typography markup."""

    header: list[str] = Field(default_factory=list)
    lines: list[str] = Field(
        description=(
            "Every visible body line in reading order as plain text. "
            "Do not emit <b>, <i>, or other markup tags."
        )
    )
    footer: list[str] = Field(default_factory=list)


class ColumnTranscriptionPlain(BaseModel):
    """Lines from a single detected column without typography markup."""

    column_id: str = Field(
        description=(
            "Column identifier. Use 'left', 'center', 'right' for multi-column pages, "
            "or 'single' when the page has only one column."
        )
    )
    lines: list[str] = Field(
        description=(
            "Every visible line of text in this column exactly as it appears, "
            "one string per line, top to bottom. Preserve all diacritics, stress marks, "
            "and special characters. Do not merge, skip, or paraphrase any line. "
            "Plain text only — do not emit <b>, <i>, or other markup tags."
        )
    )


class TranscriptionResponse(BaseModel):
    """Structured output schema for Stage 1 column transcription."""

    header: list[str] = Field(default_factory=list)
    columns: list[ColumnTranscription] = Field(
        description="Body columns left → right; transcribe each fully top → bottom."
    )
    footer: list[str] = Field(default_factory=list)


class TranscriptionResponsePlain(BaseModel):
    """Structured output for Stage 1 column transcription without typography markup."""

    header: list[str] = Field(default_factory=list)
    columns: list[ColumnTranscriptionPlain] = Field(
        description="Body columns left → right; transcribe each fully top → bottom."
    )
    footer: list[str] = Field(default_factory=list)

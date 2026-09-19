"""Pydantic schemas for notes and vault file operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateNoteInput(BaseModel):
    """Create or update a note (body is written to the vault ``.md``)."""

    title: str | None = None
    content: str = ""
    created_at: datetime | None = None
    # Vault-relative folder (e.g. "Life/Daily Log")
    folder: str | None = None


class MoveNoteInput(BaseModel):
    """Move a note into a folder (empty string = vault root)."""

    folder: str = ""


class MoveVaultFileInput(BaseModel):
    """Move any vault file (note or attachment) to a new relative path."""

    from_rel: str
    to_rel: str


class DeleteVaultFileInput(BaseModel):
    """Delete a vault attachment and strip markdown links that pointed at it."""

    rel_path: str


class BatchDeleteNotesInput(BaseModel):
    """Delete many notes in the current KB."""

    ids: list[str] = Field(default_factory=list)


class MkdirInput(BaseModel):
    """Create an empty folder in the vault."""

    path: str = ""

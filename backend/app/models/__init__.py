"""SQLAlchemy ORM models — SQLite metadata for Orb desktop.

Note bodies live in vault ``.md`` files, not in the DB.
"""

from app.models.kb import KnowledgeBase
from app.models.note import Note
from app.models.wikilink import NoteLink

__all__ = [
    "KnowledgeBase",
    "Note",
    "NoteLink",
]

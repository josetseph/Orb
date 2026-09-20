"""`_sqlite_repairs` drops the dead `notes.content` column only when it is empty."""

from sqlalchemy import create_engine

from app.core.database import _sqlite_repairs

DDL = "CREATE TABLE notes (id TEXT PRIMARY KEY, kb_id TEXT, rel_path TEXT, content TEXT)"


def _cols(conn):
    return {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(notes)")}


def test_drops_empty_content_column(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(DDL)
        conn.exec_driver_sql("INSERT INTO notes VALUES ('1', 'k', 'a.md', '')")
        _sqlite_repairs(conn)
        assert "content" not in _cols(conn)
        _sqlite_repairs(conn)  # idempotent


def test_keeps_column_while_bodies_remain(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(DDL)
        conn.exec_driver_sql("INSERT INTO notes VALUES ('1', 'k', 'a.md', 'body')")
        _sqlite_repairs(conn)
        assert "content" in _cols(conn)


def test_adds_chat_summary_columns(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    with eng.begin() as conn:
        conn.exec_driver_sql(DDL)
        conn.exec_driver_sql("CREATE TABLE chat_conversations (id TEXT PRIMARY KEY, kb_id TEXT)")
        _sqlite_repairs(conn)
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(chat_conversations)")}
        assert {"summary", "summary_message_count"} <= cols
        _sqlite_repairs(conn)  # idempotent

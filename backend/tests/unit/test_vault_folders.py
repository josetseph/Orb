"""Folders move and delete as a unit; the file endpoints accept them."""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.vault_ops import delete_vault_file, move_vault_file


class _NoNotes:
    async def execute(self, _q):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    async def commit(self):
        pass


def _kb(tmp_path):
    (tmp_path / "Papers" / "2026").mkdir(parents=True)
    (tmp_path / "Papers" / ".keep").write_text("")
    (tmp_path / "Papers" / "a.pdf").write_text("a")
    (tmp_path / "Papers" / "2026" / "b.pdf").write_text("b")
    (tmp_path / "Archive").mkdir()
    return SimpleNamespace(vault_path=str(tmp_path), kb_id="kb")


def test_move_folder_carries_everything(tmp_path):
    kb = _kb(tmp_path)
    out = asyncio.run(move_vault_file(_NoNotes(), kb, "Papers", "Archive/Papers"))
    assert out["to"] == "Archive/Papers" and out["moved"] == 2
    assert not (tmp_path / "Papers").exists()
    assert (tmp_path / "Archive/Papers/a.pdf").read_text() == "a"
    assert (tmp_path / "Archive/Papers/2026/b.pdf").read_text() == "b"
    assert (tmp_path / "Archive/Papers/.keep").exists()


def test_cannot_move_folder_into_itself(tmp_path):
    kb = _kb(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(move_vault_file(_NoNotes(), kb, "Papers", "Papers/2026/Papers"))
    assert (tmp_path / "Papers" / "a.pdf").exists()


def test_attachments_stay_under_attachments(tmp_path):
    kb = _kb(tmp_path)
    (tmp_path / "attachments" / "a").mkdir(parents=True)
    (tmp_path / "attachments" / "a" / "x.pdf").write_text("x")
    (tmp_path / "Papers" / "n.md").write_text("n")
    with pytest.raises(ValueError, match="attachments/ boundary"):
        asyncio.run(move_vault_file(_NoNotes(), kb, "attachments/a/x.pdf", "Papers/x.pdf"))
    with pytest.raises(ValueError, match="attachments/ boundary"):
        asyncio.run(move_vault_file(_NoNotes(), kb, "Papers/n.md", "attachments/n.md"))
    with pytest.raises(ValueError, match="attachments/ boundary"):
        asyncio.run(move_vault_file(_NoNotes(), kb, "attachments/a", "Papers/a"))
    out = asyncio.run(move_vault_file(_NoNotes(), kb, "attachments/a/x.pdf", "attachments/b/x.pdf"))
    assert out["to"] == "attachments/b/x.pdf" and (tmp_path / "attachments/b/x.pdf").exists()
    out = asyncio.run(move_vault_file(_NoNotes(), kb, "Papers/n.md", "Archive/n.md"))
    assert out["to"] == "Archive/n.md"


def test_delete_folder_removes_tree(tmp_path):
    kb = _kb(tmp_path)
    out = asyncio.run(delete_vault_file(_NoNotes(), kb, "Papers"))
    assert out["deleted"] == "Papers"
    assert not (tmp_path / "Papers").exists()
    assert (tmp_path / "Archive").exists()


def test_move_rewrites_the_extraction_marker_with_the_link():
    from app.services.vault_ops import rewrite_refs_in_text

    note = (
        "[📎 Audio.m4a](/vault-files/kb/attachments/Audio.m4a)\n\n"
        '<!-- orb:extract src="/vault-files/kb/attachments/Audio.m4a" -->\n'
        "Speaker 1: hello\n<!-- /orb:extract -->"
    )
    out = rewrite_refs_in_text(note, "attachments/Audio.m4a", "attachments/Lectures/Audio.m4a")
    # Emits the canonical relative form whatever kb id the old link carried.
    assert out.count("](attachments/Lectures/Audio.m4a)") == 1
    assert out.count('src="attachments/Lectures/Audio.m4a"') == 1
    assert "vault-files" not in out and "attachments/Audio.m4a" not in out


class TestStripRefs:
    """Deleting an attachment removes only its links and leaves other notes byte-identical."""

    def test_removes_every_form_of_the_link(self):
        from app.services.vault_ops import strip_refs_in_text

        body = (
            "intro\n\n[📎 a b.pdf](attachments/Q3/a%20b.pdf)\n\n"
            "![img](/vault-files/6e9ecae6-old/attachments/Q3/a%20b.pdf)\n\n"
            "[raw](attachments/Q3/a b.pdf)\n\nkeep [other](attachments/Q3/other.pdf)\n"
        )
        out = strip_refs_in_text(body, "attachments/Q3/a b.pdf")
        assert "a%20b.pdf" not in out and "a b.pdf" not in out
        assert "[other](attachments/Q3/other.pdf)" in out
        assert "\n\n\n" not in out

    def test_unrelated_note_is_untouched_even_with_blank_runs(self):
        from app.services.vault_ops import strip_refs_in_text

        body = "para one\n\n\n\npara two [x](attachments/x.pdf)\n"
        assert strip_refs_in_text(body, "attachments/Q3/a b.pdf") is body

    def test_basename_alone_does_not_match(self):
        from app.services.vault_ops import strip_refs_in_text

        body = "[same name elsewhere](attachments/Other/a%20b.pdf)\n"
        assert strip_refs_in_text(body, "attachments/Q3/a b.pdf") == body

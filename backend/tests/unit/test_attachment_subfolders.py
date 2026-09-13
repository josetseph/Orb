"""Attachments organise into subfolders.

mkdir and move_vault_file already supported it; the listing did not, so a file
moved into a subfolder disappeared from the UI while still on disk and still
linked from its note.
"""

from app.services.vault_sync import list_attachment_files, list_vault_folders


def _vault(tmp_path):
    att = tmp_path / "attachments"
    (att / "Papers" / "2026").mkdir(parents=True)
    (att / "top.pdf").write_text("x")
    (att / "Papers" / "mid.pdf").write_text("x")
    (att / "Papers" / "2026" / "deep.pdf").write_text("x")
    return tmp_path


def test_finds_files_at_every_depth(tmp_path):
    rels = {f["rel_path"] for f in list_attachment_files(_vault(tmp_path))}
    assert rels == {
        "attachments/top.pdf",
        "attachments/Papers/mid.pdf",
        "attachments/Papers/2026/deep.pdf",
    }


def test_name_is_the_filename_not_the_path(tmp_path):
    by_rel = {f["rel_path"]: f["name"] for f in list_attachment_files(_vault(tmp_path))}
    assert by_rel["attachments/Papers/2026/deep.pdf"] == "deep.pdf"


def test_hidden_files_and_folders_are_skipped(tmp_path):
    v = _vault(tmp_path)
    (v / "attachments" / ".DS_Store").write_text("x")
    (v / "attachments" / ".trash").mkdir()
    (v / "attachments" / ".trash" / "old.pdf").write_text("x")
    rels = {f["rel_path"] for f in list_attachment_files(v)}
    assert not any(".DS_Store" in r or ".trash" in r for r in rels)


def test_directories_are_not_listed_as_files(tmp_path):
    rels = {f["rel_path"] for f in list_attachment_files(_vault(tmp_path))}
    assert "attachments/Papers" not in rels


def test_attachment_subfolders_appear_as_folders(tmp_path):
    folders = list_vault_folders(_vault(tmp_path))
    assert "attachments" in folders
    assert "attachments/Papers" in folders
    assert "attachments/Papers/2026" in folders


def test_empty_vault_is_fine(tmp_path):
    assert list_attachment_files(tmp_path) == []

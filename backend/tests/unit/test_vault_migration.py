"""One-time vault sweep: fixes legacy link shapes in place, once."""

from app.services import vault_sync
from app.workflows.agents import ingestion_agent


def test_sweep_rewrites_once(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ingestion_agent, "wrap_legacy_enrichment_blocks", lambda t: t.replace("LEGACY", "WRAPPED")
    )
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "x.md").write_text("![](/vault-files/kb/attachments/attachments/a.png)")
    note = tmp_path / "n.md"
    note.write_text(
        "![a](/vault-files/kb/attachments/attachments/a.png)\n"
        "![b](attachments/attachments/b.png)\n"
        "[c](/vault-files/kb/attachments/My%20File (1).png)\n"
        '<!-- orb:extract src="/vault-files/kb/attachments/My%20File (1).png" -->\n'
        "[d](/vault-files/kb/attachments/d%2520.png)\n"
        "LEGACY\n"
        "[[Other Note]] plain text (not a link)\n"
    )
    clean = tmp_path / "clean.md"
    clean.write_text("nothing to do\n")

    assert vault_sync.migrate_vault_files(tmp_path) == 1
    assert note.read_text() == (
        "![a](attachments/a.png)\n"
        "![b](attachments/b.png)\n"
        "[c](attachments/My%20File%20%281%29.png)\n"
        '<!-- orb:extract src="attachments/My%20File%20%281%29.png" -->\n'
        "[d](attachments/d%2520.png)\n"
        "WRAPPED\n"
        "[[Other Note]] plain text (not a link)\n"
    )
    assert clean.read_text() == "nothing to do\n"
    assert "attachments/attachments" in (tmp_path / "attachments" / "x.md").read_text()
    assert (tmp_path / ".orb" / "migrated-v2").exists()

    note.write_text("![b](attachments/attachments/b.png)")
    assert vault_sync.migrate_vault_files(tmp_path) == 0
    assert note.read_text() == "![b](attachments/attachments/b.png)"


def test_v2_relativises_links_from_any_workspace_id(tmp_path):
    """Links minted under a dead workspace UUID become vault-relative, marker in step."""
    (tmp_path / ".orb").mkdir()
    (tmp_path / ".orb" / "migrated-v1").touch()  # a vault already at v1 still gets v2
    note = tmp_path / "n.md"
    note.write_text(
        "[📎 Talk.m4a](/vault-files/6e9ecae6-0000/attachments/Talk%20(1).m4a)\n"
        '<!-- orb:extract src="/vault-files/6e9ecae6-0000/attachments/Talk%20(1).m4a" -->\n'
        "x\n<!-- /orb:extract -->\n"
        "![ok](attachments/already.png)\n"
        "[web](https://example.com/vault-files/not/ours.png)\n"
    )
    assert vault_sync.migrate_vault_files(tmp_path) == 1
    assert note.read_text() == (
        "[📎 Talk.m4a](attachments/Talk%20%281%29.m4a)\n"
        '<!-- orb:extract src="attachments/Talk%20%281%29.m4a" -->\n'
        "x\n<!-- /orb:extract -->\n"
        "![ok](attachments/already.png)\n"
        "[web](https://example.com/vault-files/not/ours.png)\n"
    )
    assert (tmp_path / ".orb" / "migrated-v2").exists()
    before = note.read_text()
    assert vault_sync.migrate_vault_files(tmp_path) == 0
    assert note.read_text() == before

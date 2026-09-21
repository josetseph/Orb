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
    assert (tmp_path / ".orb" / "migrated-v4").exists()

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
    assert (tmp_path / ".orb" / "migrated-v4").exists()
    before = note.read_text()
    assert vault_sync.migrate_vault_files(tmp_path) == 0
    assert note.read_text() == before


def test_v3_moves_stray_files_under_attachments(tmp_path):
    """Non-markdown files beside notes move to attachments/<folder>/ and links follow."""
    (tmp_path / ".orb").mkdir()
    (tmp_path / ".orb" / "migrated-v2").touch()
    (tmp_path / "Cloud Computing").mkdir()
    (tmp_path / "Cloud Computing" / "diagram.png").write_bytes(b"png")
    (tmp_path / "Cloud Computing" / ".DS_Store").write_text("")
    (tmp_path / "root.pdf").write_text("pdf")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "root.pdf").write_text("taken")
    note = tmp_path / "Cloud Computing" / "n.md"
    note.write_text(
        "![d](Cloud%20Computing/diagram.png)\n"
        "![old](/vault-files/6e9ecae6-0000/Cloud%20Computing/diagram.png)\n"
        "[r](root.pdf)\n"
    )
    assert vault_sync.migrate_vault_files(tmp_path) == 1
    assert (tmp_path / "attachments" / "Cloud Computing" / "diagram.png").read_bytes() == b"png"
    assert not (tmp_path / "Cloud Computing" / "diagram.png").exists()
    assert (tmp_path / "Cloud Computing" / ".DS_Store").exists()
    assert (tmp_path / "attachments" / "root 2.pdf").read_text() == "pdf"
    assert note.read_text() == (
        "![d](attachments/Cloud%20Computing/diagram.png)\n"
        "![old](attachments/Cloud%20Computing/diagram.png)\n"
        "[r](attachments/root%202.pdf)\n"
    )
    assert (tmp_path / ".orb" / "migrated-v4").exists()
    before = note.read_text()
    assert vault_sync.migrate_vault_files(tmp_path) == 0
    assert note.read_text() == before


def test_v4_repoints_a_link_whose_file_moved_inside_attachments(tmp_path):
    """An older build moved the file but could not rewrite a name with parentheses."""
    name = "Prosit - Capacity Building (2)-a45eb768.pdf"
    (tmp_path / "attachments" / "Seminar").mkdir(parents=True)
    (tmp_path / "attachments" / "Seminar" / name).write_bytes(b"%PDF")
    (tmp_path / "attachments" / "kept-11111111.pdf").write_bytes(b"%PDF")
    flat = "attachments/Prosit%20-%20Capacity%20Building%20%282%29-a45eb768.pdf"
    note = tmp_path / "n.md"
    note.write_text(
        f'[📎 x]({flat})\n<!-- orb:extract src="{flat}" -->\nbody\n<!-- /orb:extract -->\n'
        "[ok](attachments/kept-11111111.pdf) [gone](attachments/nowhere-22222222.pdf)\n",
        encoding="utf-8",
    )

    assert vault_sync.migrate_vault_files(tmp_path) == 1
    text = note.read_text(encoding="utf-8")
    fixed = "attachments/Seminar/Prosit%20-%20Capacity%20Building%20%282%29-a45eb768.pdf"
    assert f"]({fixed})" in text and f'src="{fixed}"' in text and flat not in text
    assert "(attachments/kept-11111111.pdf)" in text  # existing target untouched
    assert "(attachments/nowhere-22222222.pdf)" in text  # no home: left alone

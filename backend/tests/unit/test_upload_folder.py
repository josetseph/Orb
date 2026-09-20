"""Uploads group under attachments/<note folder>/."""

import asyncio

import pytest

from app.services.local_storage import store_upload


def _store(tmp_path, folder):
    return asyncio.run(store_upload(tmp_path, "logo.png", b"x", "kb", folder))


def test_upload_lands_in_the_note_folder(tmp_path):
    out = _store(tmp_path, "Natural Language Processing/Prosit 1")
    assert out["key"].startswith("attachments/Natural Language Processing/Prosit 1/logo-")
    assert out["url"] == f"/vault-files/kb/{out['key']}"
    assert (tmp_path / out["key"]).read_bytes() == b"x"


def test_root_note_uploads_flat(tmp_path):
    key = _store(tmp_path, "")["key"]
    assert key.startswith("attachments/logo-") and key.count("/") == 1


@pytest.mark.parametrize("folder", ["../etc", "a/../../b", "/etc"])
def test_traversal_rejected(tmp_path, folder):
    with pytest.raises(ValueError):
        _store(tmp_path, folder)
    assert not list(tmp_path.rglob("logo-*"))

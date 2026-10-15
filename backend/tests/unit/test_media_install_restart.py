"""Installing media packages under a running backend must ask for a restart
when it replaced modules the process had already imported."""

import sys
import types

import pytest

from app.services import multimodal_services as ms


@pytest.fixture()
def user_site(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path))
    monkeypatch.setattr(ms.site, "getusersitepackages", lambda: str(tmp_path))
    monkeypatch.setattr(ms.site, "addsitedir", lambda d: None)
    monkeypatch.setattr(ms.subprocess, "check_call", lambda cmd: 0)
    monkeypatch.setattr(ms, "_deps_importable", lambda: (False, "missing"))
    monkeypatch.setattr(ms, "_restart_needed", False)
    return tmp_path


def test_upgrading_a_loaded_package_needs_a_restart(user_site, monkeypatch):
    mod = types.ModuleType("fake_transformers")
    mod.__file__ = str(user_site / "fake_transformers" / "__init__.py")
    monkeypatch.setitem(sys.modules, "fake_transformers", mod)

    result = ms.ensure_multimodal_python_deps(install=True)

    assert result["restart_required"] and ms.restart_needed()
    from app.services.multimodal_runtime import multimodal_runtime

    with pytest.raises(RuntimeError, match="restart Orb"):
        multimodal_runtime._load_phonon()


def test_first_install_loads_in_place(user_site, monkeypatch):
    monkeypatch.setattr(ms, "_deps_importable", iter([(False, "missing"), (True, None)]).__next__)
    result = ms.ensure_multimodal_python_deps(install=True)
    assert result["ok"] and not result.get("restart_required") and not ms.restart_needed()

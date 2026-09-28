"""A note the pipeline rejected is a result, not an outage: the ingest carries on past any number of them."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import prepare_dataset  # noqa: E402


def test_rejected_notes_do_not_stop_the_ingest(tmp_path, monkeypatch):
    notes = tmp_path / "hotpotqa_notes"
    notes.mkdir()
    names = [f"n{i}.md" for i in range(6)]
    for name in names:
        (notes / name).write_text(f"# {name}\n\nbody of {name}\n")
    manifest = {"dataset": "hotpotqa", "notes_dir": "hotpotqa_notes", "test_cases": [{"id": "q", "all_notes": names}]}
    (tmp_path / "hotpotqa_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(prepare_dataset, "BASE_DIR", tmp_path)
    monkeypatch.setattr(prepare_dataset, "PROGRESS_FILE", tmp_path / "progress.json")
    created = []

    async def create(client, content, title):
        created.append(title)
        return f"id-{len(created)}"

    async def wait(client, note_id, poll_interval=0):
        return note_id == "id-6"  # the first five are rejected by the pipeline

    monkeypatch.setattr(prepare_dataset, "_create_and_ingest", create)
    monkeypatch.setattr(prepare_dataset, "_wait_for_completion", wait)
    asyncio.run(prepare_dataset.prepare("hotpotqa", limit=None, resume=False, dry_run=False))
    states = json.loads((tmp_path / "progress.json").read_text())["hotpotqa"]
    assert len(created) == 6
    assert list(states.values()).count("failed") == 5 and states["n5.md"] == "id-6"

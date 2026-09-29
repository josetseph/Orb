"""The follower survives a lost network: it waits and retries instead of exiting."""

import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import pod  # noqa: E402


def test_network_errors_are_retried_until_the_round_finishes(monkeypatch):
    calls = []

    def once(state, args):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("nodename nor servname provided")
        return True

    monkeypatch.setattr(pod, "saved", lambda: {"id": "p"})
    monkeypatch.setattr(pod, "follow_once", once)
    monkeypatch.setattr(pod.time, "sleep", lambda s: None)
    pod.cmd_follow(SimpleNamespace(every=15))
    assert len(calls) == 3

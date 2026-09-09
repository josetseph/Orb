"""IngestionWorkflow needs the kb_id it namespaces tracker state with.

It reads self.kb_id in five places — begin_ingestion, community rebuild,
pending-node registration and get_maintenance_status — but __init__ never
assigned it, so every maintenance-status poll raised AttributeError and the
endpoint 500'd every 30 seconds.
"""

from app.workflows.ingestion import IngestionWorkflow


def _wf(**kw):
    return IngestionWorkflow.__new__(IngestionWorkflow)


def test_default_kb_id():
    import inspect

    sig = inspect.signature(IngestionWorkflow.__init__)
    assert "kb_id" in sig.parameters
    assert sig.parameters["kb_id"].default == "default"


def test_maintenance_status_reads_kb_id():
    wf = _wf()
    wf.kb_id = "research"
    wf._community_run_running = False
    wf._temporal_digest_running = False
    status = wf.get_maintenance_status()
    assert status["healthy"] is True
    assert "active" in status["ingestion"]


def test_missing_kb_id_would_raise():
    """Guards the regression: no attribute, no status."""
    wf = _wf()
    wf._community_run_running = False
    wf._temporal_digest_running = False
    try:
        wf.get_maintenance_status()
    except AttributeError as exc:
        assert "kb_id" in str(exc)
    else:
        raise AssertionError("expected AttributeError without kb_id")

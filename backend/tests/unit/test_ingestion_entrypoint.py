"""The agent entry point must hand extraction the note text and a log list.

The attachment stage that was cut from this branch used to set both; losing them
made every ingest fail (missing key) or, with only the key fixed, extract from "".
"""

import asyncio
from types import SimpleNamespace

from app.workflows.agents import ingestion_agent as agent


def test_entrypoint_seeds_content_and_logs(monkeypatch):
    seen = {}

    async def fake_extraction(state):
        seen.update(content=state["content"], logs=state["logs"])
        return {"errors": ["stop here"]}

    monkeypatch.setattr(agent, "extraction_node", fake_extraction)
    state = {"input": SimpleNamespace(content="  Alex Ferguson managed Manchester United.  "), "content": "", "errors": []}
    asyncio.run(agent.run_ingestion_agent(state))
    assert seen == {"content": "Alex Ferguson managed Manchester United.", "logs": []}

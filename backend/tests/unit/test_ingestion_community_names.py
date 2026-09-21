"""A community's name and summary are the model's, as written, or the community is not created."""

import json

import pytest

from app.services.model_output import ModelOutputError
from app.workflows.ingestion import IngestionWorkflow

MEMBERS = [{"name": "Battle of Adwa"}, {"name": "Menelik II"}, {"name": "Ethiopia"}]


def _workflow(reply: str) -> IngestionWorkflow:
    class LLM:
        prompts = []

        async def ingestion_generate(self, prompt, temperature=0.1, **kw):
            self.prompts.append((prompt, kw))
            return reply

        def get_ingestion_model(self):
            return "stub"

    wf = IngestionWorkflow.__new__(IngestionWorkflow)
    wf._llm = LLM()
    return wf


def test_name_and_summary_are_taken_as_written():
    wf = _workflow(json.dumps({"name": "Adwa", "summary": "The battle."}))
    assert wf._build_community_summary(MEMBERS, 2) == ("Adwa", "The battle.")
    prompt, kw = wf._llm.prompts[0]
    assert kw.get("json_mode") is True and '"name"' in prompt and "NAME:" not in prompt


@pytest.mark.parametrize(
    "reply",
    ["Adwa: the battle", json.dumps({"name": "", "summary": "x"}), json.dumps({"name": "Adwa"}), json.dumps({"title": "Adwa", "summary": "x"})],
    ids=["not-json", "empty-name", "no-summary", "wrong-key"],
)
def test_no_name_is_invented_for_an_unusable_reply(reply):
    with pytest.raises(ModelOutputError):
        _workflow(reply)._build_community_summary(MEMBERS, 2)

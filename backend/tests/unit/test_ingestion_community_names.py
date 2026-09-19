"""Community names come back as JSON and must be anchored in a member entity."""

import json

from app.workflows.ingestion import IngestionWorkflow

MEMBERS = [{"name": "Battle of Adwa"}, {"name": "Menelik II"}, {"name": "Ethiopia"}]


def test_name_must_share_a_word_with_a_member():
    fits = IngestionWorkflow._name_fits_members
    assert fits("Ethiopia's Victory at Adwa", MEMBERS)
    assert not fits("Transient Echoes", MEMBERS)
    assert not fits("Community L2-14", MEMBERS)
    assert not fits("", MEMBERS) and not fits(None, MEMBERS)
    assert not fits("of", MEMBERS), "tokens under 3 letters do not count"


def test_name_and_summary_parse_json():
    class LLM:
        prompts = []

        async def ingestion_generate(self, prompt, temperature=0.1, **kw):
            self.prompts.append((prompt, kw))
            return json.dumps({"name": "Adwa", "summary": "The battle."})

        def _clean_json(self, raw):
            return raw

    wf = IngestionWorkflow.__new__(IngestionWorkflow)
    wf._llm = LLM()
    assert wf._build_community_summary(MEMBERS, 2) == ("Adwa", "The battle.")
    prompt, kw = wf._llm.prompts[0]
    assert kw.get("json_mode") is True and '"name"' in prompt and "NAME:" not in prompt

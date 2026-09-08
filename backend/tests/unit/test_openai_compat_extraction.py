"""openai_compat must reach the OpenAI structured-extraction path.

It was missing from the dispatch, so every query-analysis call on a KB pinned
to an OpenAI-compatible endpoint raised "Unsupported provider: openai_compat",
returned nothing, and left the chat loop retrieving blind.
"""

from app.services.llm import LLMService


def _svc(provider, chat_model):
    s = LLMService.__new__(LLMService)
    s.provider = provider
    s._chat_model_override = chat_model
    s._ingestion_model_override = None
    return s


def test_openai_compat_is_dispatched(monkeypatch):
    import app.services.llm as m

    seen = {}
    monkeypatch.setattr(
        LLMService, "_extract_openai", lambda self, *a, **k: seen.setdefault("hit", True)
    )
    svc = _svc("openai_compat", "models/gemini-3.8-flash")
    svc.extract_structured("p", dict, 0.1)
    assert seen.get("hit") is True


def test_model_comes_from_the_pin_not_openai_settings():
    """settings.OPENAI_MODEL is wrong for a Gemini-compat endpoint."""
    svc = _svc("openai_compat", "models/gemini-3.8-flash")
    assert svc.get_chat_model() == "models/gemini-3.8-flash"

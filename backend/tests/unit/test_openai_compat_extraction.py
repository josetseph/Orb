"""A KB pinned to an OpenAI-compatible endpoint must use its pinned model."""

from app.services.llm import LLMService


def _svc(provider, chat_model):
    s = LLMService.__new__(LLMService)
    s.provider = provider
    s._chat_model_override = chat_model
    s._ingestion_model_override = None
    return s


def test_model_comes_from_the_pin_not_openai_settings():
    """settings.OPENAI_MODEL is wrong for a Gemini-compat endpoint."""
    svc = _svc("openai_compat", "models/gemini-3.8-flash")
    assert svc.get_chat_model() == "models/gemini-3.8-flash"

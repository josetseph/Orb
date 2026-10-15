"""A saved reply that turned out unusable must not replay on the retry."""

import asyncio

from app.services import ingestion_checkpoint as checkpoint


class _LLM:
    def __init__(self):
        self.calls = 0

    def get_ingestion_model(self):
        return "m"

    async def ingestion_generate_with_meta(self, prompt, temperature=0.1, json_mode=False):
        self.calls += 1
        return f"reply {self.calls}", {}


def test_forget_makes_the_retry_ask_again():
    llm = _LLM()

    async def main():
        checkpoint.activate("kb-test", "note-forget")
        try:
            first, _ = await checkpoint.generate_with_meta(llm, "p")
            replayed, _ = await checkpoint.generate_with_meta(llm, "p")
            checkpoint.forget(llm, "p")
            fresh, _ = await checkpoint.generate_with_meta(llm, "p")
            return first, replayed, fresh
        finally:
            checkpoint.clear("kb-test", "note-forget")

    assert asyncio.run(main()) == ("reply 1", "reply 1", "reply 2")

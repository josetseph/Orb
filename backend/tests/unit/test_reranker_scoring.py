"""Reranker scores come from the last position's yes/no logits, reusing the prompt prefix shared with the last document."""
import math
import threading

from app.services.local_models import LocalGgufReranker


class FakeModel:
    def __init__(self):
        self.input_ids, self.n_tokens, self.evaluated = [0] * 4096, 0, []
        self._ctx = self
        self.logits = {}

    def n_ctx(self):
        return 4096

    def tokenize(self, text, special=True):
        return list(text)  # one token per byte

    def eval(self, tokens):
        self.evaluated.append(len(tokens))
        self.input_ids[self.n_tokens : self.n_tokens + len(tokens)] = tokens
        self.n_tokens += len(tokens)

    def get_logits_ith(self, i):
        return self.logits


def test_score_is_the_yes_no_softmax_and_the_shared_prefix_is_kept():
    r = LocalGgufReranker.__new__(LocalGgufReranker)
    r._lock, r._model, r._yes_id, r._no_id = threading.RLock(), FakeModel(), 1, 2
    r._model.logits = {1: 2.0, 2: 0.0}
    assert abs(r._score_one("q", "a document") - 1 / (1 + math.exp(-2.0))) < 1e-12
    first = r._model.evaluated[-1]
    r._model.logits = {1: -50.0, 2: 50.0}
    assert r._score_one("q", "another one") < 1e-40
    assert r._model.evaluated[-1] < first  # only the part after the shared system/instruction/query prefix
    r._score_one("q", "another one")  # identical prompt: still evaluates one token for fresh logits
    assert r._model.evaluated[-1] == 1

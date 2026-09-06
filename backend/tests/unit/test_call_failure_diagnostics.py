"""The OpenAI SDK reports every transport fault as "Connection error."

DNS failure, refused connection, TLS error and a dropped socket all produce the
same string, which makes a real outage indistinguishable from a misconfigured
endpoint. The cause is in the exception chain.
"""

from app.services.llm import describe_call_failure


class TestDescribeCallFailure:
    def test_names_the_underlying_cause(self):
        root = ConnectionRefusedError(61, "Connection refused")
        try:
            try:
                raise root
            except ConnectionRefusedError as inner:
                raise RuntimeError("Connection error.") from inner
        except RuntimeError as exc:
            out = describe_call_failure(exc)
        assert "Connection error." in out
        assert "ConnectionRefusedError" in out and "refused" in out

    def test_plain_exception_still_reads_well(self):
        assert describe_call_failure(ValueError("bad model")) == "ValueError: bad model"

    def test_chain_is_capped(self):
        exc = ValueError("deepest")
        for i in range(8):
            try:
                raise exc
            except Exception as inner:  # noqa: BLE001
                exc = RuntimeError(f"layer {i}")
                exc.__cause__ = inner
        assert describe_call_failure(exc).count("←") <= 3

    def test_self_referencing_chain_terminates(self):
        exc = RuntimeError("loop")
        exc.__cause__ = exc
        assert describe_call_failure(exc) == "RuntimeError: loop"

    def test_empty_message_still_names_the_type(self):
        inner = TimeoutError()
        outer = RuntimeError("failed")
        outer.__cause__ = inner
        assert "TimeoutError" in describe_call_failure(outer)

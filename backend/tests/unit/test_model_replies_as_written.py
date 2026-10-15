"""Model replies are parsed exactly as written: nothing unwraps, re-quotes or repairs them."""

import pytest
from pydantic import ValidationError

from app.schemas.extraction import Extraction


def test_a_fenced_reply_is_rejected_not_unwrapped():
    with pytest.raises(ValidationError):
        Extraction.model_validate_json('```json\n{"nodes": [], "relationships": []}\n```')


def test_valid_json_is_used_as_is():
    assert Extraction.model_validate_json('{"nodes": [], "relationships": []}').nodes == []

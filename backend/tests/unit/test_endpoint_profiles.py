"""One server, several keys: the profile name in the fragment is part of the identity."""

from app.services.credentials import (
    endpoint_credential_id,
    endpoint_profile,
    normalize_base_url,
    request_base_url,
)

GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai"


def test_profiles_on_one_server_are_distinct_credentials():
    personal = endpoint_credential_id(f"{GOOGLE}#Personal")
    work = endpoint_credential_id(f"{GOOGLE}#Work")
    plain = endpoint_credential_id(GOOGLE)
    assert len({personal, work, plain}) == 3


def test_profile_spelling_is_canonical():
    a = normalize_base_url(f"{GOOGLE}/#Gemini - Personal")
    b = normalize_base_url(f"HTTPS://generativelanguage.googleapis.com/v1beta/openai#Gemini%20-%20Personal")
    c = normalize_base_url(f"{GOOGLE}#  Gemini   -  Personal ")
    assert a == b == c == f"{GOOGLE}#Gemini - Personal"


def test_requests_never_carry_the_profile():
    assert request_base_url(f"{GOOGLE}#Work") == GOOGLE
    assert request_base_url(f"{GOOGLE}/") == GOOGLE


def test_profile_is_readable():
    assert endpoint_profile(f"{GOOGLE}#Work") == "Work"
    assert endpoint_profile(GOOGLE) == ""

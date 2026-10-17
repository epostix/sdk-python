from __future__ import annotations

from typing import Any

import pytest
from conftest import Clients, Recorder, Reply, codec_for, load_fixture

from epostix import (
    DEFAULT_RETRY_POLICY,
    EpostixAPIError,
    EpostixDecodeError,
    EpostixQuotaExceededError,
    EpostixRateLimitError,
    is_known_enum_value,
    response_metadata_of,
    unknown_fields_of,
)
from epostix._generated.codecs import CODEC_API_KEY_RESPONSE, CODEC_EMAIL, CODEC_SUPPRESSION
from epostix._retry import ClassifyContext, classify_attempt
from epostix.models import EmailStatus, SuppressionReason

FIXTURE = load_fixture("decoding")
CASES = {case["name"]: case for case in FIXTURE["cases"]}

MODEL_CASES = [case for case in FIXTURE["cases"] if "status" not in case]
WIRE_CASES = [case for case in FIXTURE["cases"] if "status" in case]


def is_retryable(error: BaseException) -> bool:
    decision = classify_attempt(
        ClassifyContext(
            policy=DEFAULT_RETRY_POLICY,
            retry_class="SafeRead",
            attempt_number=1,
            remaining=DEFAULT_RETRY_POLICY.operation_deadline,
            error=error,
            has_idempotency_key=False,
            body_is_replayable=True,
            bytes_were_written=False,
            response_started=False,
            random=lambda: 0.5,
        )
    )

    return decision.action == "retry"


def fetch(clients: Clients, case: dict[str, Any]) -> tuple[Any, BaseException | None]:
    reply = Reply(
        status=case["status"],
        headers=case.get("headers", {}),
    )

    if case.get("type") == "raw":
        reply.text = case["body"]
        reply.headers = {**reply.headers, "content-type": case["contentType"]}
    else:
        reply.body = case["wire"]

    client = clients.sync(Recorder([reply]))

    try:
        return client.emails.get_email("eml_1"), None
    except EpostixAPIError as error:
        return None, error


@pytest.mark.parametrize("case", MODEL_CASES, ids=lambda case: str(case["name"]))
def test_model_decoding_matches_the_shared_corpus(case: dict[str, Any]) -> None:
    codec = codec_for(case["type"])
    expect = case["expect"]

    if expect.get("error") == "protocol":
        with pytest.raises(EpostixDecodeError):
            codec.decode(case["wire"])
        return

    decoded = codec.decode(case["wire"])

    if "status" in expect:
        assert decoded.status == expect["status"]

    if "statusIsKnown" in expect:
        assert is_known_enum_value(EmailStatus, str(decoded.status)) is expect["statusIsKnown"]

    if "id" in expect:
        assert decoded.id == expect["id"]

    if "unknownFields" in expect:
        assert unknown_fields_of(decoded) == expect["unknownFields"]

    if "attachment_id" in expect:
        assert decoded.attachment_id == expect["attachment_id"]

    if "variant" in expect:
        assert type(decoded).__name__ == expect["variant"]


@pytest.mark.parametrize("case", WIRE_CASES, ids=lambda case: str(case["name"]))
def test_wire_decoding_matches_the_shared_corpus(clients: Clients, case: dict[str, Any]) -> None:
    expect = case["expect"]
    result, error = fetch(clients, case)

    if "class" not in expect:
        assert error is None
        assert result is not None

        metadata = response_metadata_of(result)
        assert metadata is not None

        if "idempotentReplayed" in expect:
            assert metadata.idempotent_replayed is expect["idempotentReplayed"]

        return

    assert isinstance(error, EpostixAPIError)
    assert type(error).__name__ == expect["class"]

    if "type" in expect:
        assert error.type == expect["type"]

    if "isKnownType" in expect:
        assert error.is_known_type is expect["isKnownType"]

    if "requestId" in expect:
        assert error.request_id == expect["requestId"]

    if expect.get("rawBodySnippetPresent"):
        assert error.raw_body_snippet

    if "fieldErrors" in expect:
        assert len(error.details) == expect["fieldErrors"]

    if "retryable" in expect:
        assert is_retryable(error) is expect["retryable"]


def test_the_raw_error_type_string_is_kept_exactly(clients: Clients) -> None:
    _, error = fetch(clients, CASES["unknown_error_type_degrades_by_status"])

    assert isinstance(error, EpostixAPIError)
    assert error.type == "quantum_overload"
    assert error.is_known_type is False


def test_rate_limit_headers_are_parsed(clients: Clients) -> None:
    _, error = fetch(clients, CASES["rate_limit_headers_parsed"])

    assert isinstance(error, EpostixRateLimitError)
    assert error.retry_after == 2

    snapshot = error.rate_limit
    assert snapshot is not None
    assert snapshot.limit == 100
    assert snapshot.remaining == 0
    assert snapshot.reset_after == 2
    assert snapshot.policy_burst == 100
    assert snapshot.policy_window == 60
    assert snapshot.scope == "workspace"


def test_a_daily_quota_reports_guidance_but_is_never_retried(clients: Clients) -> None:
    _, error = fetch(clients, CASES["daily_quota_never_sleeps"])

    assert isinstance(error, EpostixQuotaExceededError)
    assert error.quota_period == "daily"
    assert error.retry_after == 31200
    assert error.next_attempt_at is not None
    assert is_retryable(error) is False


def test_a_monthly_quota_reports_no_next_attempt(clients: Clients) -> None:
    _, error = fetch(clients, CASES["monthly_quota_has_no_next_attempt"])

    assert isinstance(error, EpostixQuotaExceededError)
    assert error.quota_period == "monthly"
    assert error.retry_after is None
    assert error.next_attempt_at is None
    assert is_retryable(error) is False


def test_an_unknown_enum_member_never_fails_the_enclosing_response() -> None:
    email = CODEC_EMAIL.decode(
        {
            "id": "eml_1",
            "status": "quantum_delivered",
            "created_at": "2026-09-06T10:00:00Z",
            "to": ["rae@example.com"],
        }
    )

    assert email.status == "quantum_delivered"
    assert email.to == ["rae@example.com"]
    assert is_known_enum_value(EmailStatus, str(email.status)) is False


def test_a_known_enum_member_decodes_to_the_enum() -> None:
    email = CODEC_EMAIL.decode(
        {"id": "eml_1", "status": "delivered", "created_at": "2026-09-06T10:00:00Z"}
    )

    assert email.status is EmailStatus.DELIVERED
    assert email.status == "delivered"


def test_a_cleaned_suppression_decodes_to_a_known_reason() -> None:
    suppression = CODEC_SUPPRESSION.decode(
        {
            "id": "sup_1",
            "email": "reader@lumenmail.eu",
            "reason": "cleaned",
            "created_at": "2026-10-17T10:00:00Z",
        }
    )

    assert suppression.reason is SuppressionReason.CLEANED


def test_domain_scoped_permission_grants_decode_as_strings() -> None:
    key = CODEC_API_KEY_RESPONSE.decode(
        {
            "id": "key_1",
            "name": "ci",
            "prefix": "tix_",
            "environment": "live",
            "permissions": ["sending:domain:lumenmail.eu", "emails:read"],
            "created_at": "2026-10-17T10:00:00Z",
        }
    )

    assert key.permissions == ["sending:domain:lumenmail.eu", "emails:read"]

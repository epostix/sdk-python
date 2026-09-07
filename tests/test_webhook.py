from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from conftest import load_fixture

from epostix import EpostixWebhookVerificationError
from epostix.webhooks import (
    EmailDeliveryEvent,
    EpostixWebhookVerifier,
    InboundEmailEvent,
    UnknownWebhookEvent,
    WebhookTestEvent,
    is_email_delivery_event,
    is_inbound_email_event,
    is_unknown_webhook_event,
    is_webhook_test_event,
    sign,
)

FIXTURE = load_fixture("webhook")
CASES = FIXTURE["cases"]
NOW = datetime.fromtimestamp(FIXTURE["now"], tz=timezone.utc)

VARIANTS = {
    "EmailDeliveryEvent": EmailDeliveryEvent,
    "InboundEmailEvent": InboundEmailEvent,
    "WebhookTestEvent": WebhookTestEvent,
    "UnknownWebhookEvent": UnknownWebhookEvent,
}


def verifier_for(case: dict[str, Any]) -> EpostixWebhookVerifier:
    return EpostixWebhookVerifier(
        case["secrets"],
        tolerance_seconds=FIXTURE["toleranceSeconds"],
        clock=lambda: NOW,
    )


def headers_for(case: dict[str, Any]) -> dict[str, str]:
    stamp = case.get("headerTimestamp", case["ts"])
    headers = {
        "Webhook-Timestamp": str(stamp),
        "Webhook-Id": case.get("deliveryId") or "whl_1",
    }

    if case.get("signature") is not None:
        headers["Webhook-Signature"] = case["signature"]

    return headers


@pytest.mark.parametrize("case", CASES, ids=lambda case: str(case["name"]))
def test_verification_matches_the_shared_vectors(case: dict[str, Any]) -> None:
    expect = case["expect"]
    verifier = verifier_for(case)
    payload = case["payload"].encode("utf-8")

    if not expect["valid"]:
        with pytest.raises(EpostixWebhookVerificationError) as raised:
            verifier.verify_and_decode(payload, headers_for(case))

        assert raised.value.reason == expect["reason"]
        return

    verified = verifier.verify_and_decode(payload, headers_for(case))

    if "eventType" in expect:
        assert verified.event.type == expect["eventType"]

    if "eventId" in expect:
        assert verified.event.id == expect["eventId"]

    if "deliveryId" in expect:
        assert verified.delivery.delivery_id == expect["deliveryId"]

    if expect.get("matchedSecretIndex") is not None:
        assert verified.delivery.matched_secret_index == expect["matchedSecretIndex"]

    if "variant" in expect:
        assert isinstance(verified.event, VARIANTS[expect["variant"]])


def case_named(name: str) -> dict[str, Any]:
    for case in CASES:
        if case["name"] == name:
            return case

    raise AssertionError(f"missing fixture case {name}")


def test_the_signed_event_id_is_the_deduplication_key_not_the_header_id() -> None:
    case = case_named("delivery_id_is_not_signed_and_not_the_dedup_key")
    verified = verifier_for(case).verify_and_decode(case["payload"], headers_for(case))

    assert verified.event.id == "evt_1"
    assert verified.delivery.delivery_id == "whl_999"
    assert verified.event.id != verified.delivery.delivery_id


def test_the_secret_prefix_is_part_of_the_signing_key() -> None:
    case = case_named("valid_delivered")
    secret = case["secrets"][0]

    assert secret.startswith("whsec_")
    assert sign(case["payload"].encode("utf-8"), secret, case["ts"]) in case["signature"]
    assert sign(case["payload"].encode("utf-8"), secret.removeprefix("whsec_"), case["ts"]) not in (
        case["signature"]
    )


def test_several_v1_values_are_all_considered() -> None:
    case = case_named("multiple_v1_values_during_rotation")
    verified = verifier_for(case).verify_and_decode(case["payload"], headers_for(case))

    assert verified.delivery.signature_count == 2
    assert verified.delivery.matched_secret_index == 0


def test_a_rotated_secret_still_verifies() -> None:
    case = case_named("rotation_old_secret_still_verifies")
    verified = verifier_for(case).verify_and_decode(case["payload"], headers_for(case))

    assert verified.delivery.matched_secret_index == 1


def test_an_unknown_event_type_stays_inspectable() -> None:
    case = case_named("unknown_event_type_is_inspectable")
    verified = verifier_for(case).verify_and_decode(case["payload"], headers_for(case))

    assert is_unknown_webhook_event(verified.event)
    assert verified.event.type == "email.quarantined"
    assert isinstance(verified.event, UnknownWebhookEvent)
    assert verified.event.raw_body == case["payload"]


def test_event_predicates_classify_each_family() -> None:
    delivery = verifier_for(case_named("valid_delivered")).verify_and_decode(
        case_named("valid_delivered")["payload"], headers_for(case_named("valid_delivered"))
    )
    inbound = verifier_for(case_named("valid_inbound_carries_inbound_id")).verify_and_decode(
        case_named("valid_inbound_carries_inbound_id")["payload"],
        headers_for(case_named("valid_inbound_carries_inbound_id")),
    )
    test_event = verifier_for(case_named("valid_webhook_test_is_a_known_type")).verify_and_decode(
        case_named("valid_webhook_test_is_a_known_type")["payload"],
        headers_for(case_named("valid_webhook_test_is_a_known_type")),
    )

    assert is_email_delivery_event(delivery.event)
    assert isinstance(delivery.event, EmailDeliveryEvent)
    assert delivery.event.email_id == "eml_1"

    assert is_inbound_email_event(inbound.event)
    assert isinstance(inbound.event, InboundEmailEvent)
    assert inbound.event.inbound_id == "inb_1"

    assert is_webhook_test_event(test_event.event)
    assert isinstance(test_event.event, WebhookTestEvent)
    assert test_event.event.message


def test_decoding_without_verifying_never_verifies() -> None:
    case = case_named("altered_body_fails")
    event = EpostixWebhookVerifier.decode_without_verifying(case["payload"])

    assert event.id == "evt_1"


def test_a_verifier_needs_at_least_one_secret() -> None:
    with pytest.raises(EpostixWebhookVerificationError):
        EpostixWebhookVerifier([])


def test_headers_are_matched_case_insensitively() -> None:
    case = case_named("valid_delivered")
    headers = {key.lower(): value for key, value in headers_for(case).items()}

    assert verifier_for(case).verify(case["payload"], headers).matched_secret_index == 0

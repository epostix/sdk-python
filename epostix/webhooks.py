from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence, TypeAlias, Union

from ._errors import EpostixWebhookVerificationError
from ._types import JsonValue

WEBHOOK_HEADER_ID = "Webhook-Id"
WEBHOOK_HEADER_TIMESTAMP = "Webhook-Timestamp"
WEBHOOK_HEADER_SIGNATURE = "Webhook-Signature"
DEFAULT_TOLERANCE_SECONDS = 300


class WebhookEventType(str, Enum):
    EMAIL_SENT = "email.sent"
    EMAIL_DELIVERED = "email.delivered"
    EMAIL_BOUNCED = "email.bounced"
    EMAIL_OPENED = "email.opened"
    EMAIL_CLICKED = "email.clicked"
    EMAIL_COMPLAINED = "email.complained"
    EMAIL_FAILED = "email.failed"
    EMAIL_DELAYED = "email.delayed"
    EMAIL_RECEIVED = "email.received"
    WEBHOOK_TEST = "webhook.test"


DELIVERY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        WebhookEventType.EMAIL_SENT.value,
        WebhookEventType.EMAIL_DELIVERED.value,
        WebhookEventType.EMAIL_BOUNCED.value,
        WebhookEventType.EMAIL_OPENED.value,
        WebhookEventType.EMAIL_CLICKED.value,
        WebhookEventType.EMAIL_COMPLAINED.value,
        WebhookEventType.EMAIL_FAILED.value,
        WebhookEventType.EMAIL_DELAYED.value,
    }
)


@dataclass(frozen=True)
class EmailDeliveryEvent:
    id: str
    type: str
    created_at: datetime | None
    email_id: str
    data: dict[str, JsonValue]


@dataclass(frozen=True)
class InboundEmailEvent:
    id: str
    type: str
    created_at: datetime | None
    inbound_id: str
    data: dict[str, JsonValue]


@dataclass(frozen=True)
class WebhookTestEvent:
    id: str
    type: str
    created_at: datetime | None
    message: str
    data: dict[str, JsonValue]


@dataclass(frozen=True)
class UnknownWebhookEvent:
    id: str
    type: str
    created_at: datetime | None
    data: JsonValue
    raw_body: str


EpostixWebhookEvent: TypeAlias = Union[
    EmailDeliveryEvent, InboundEmailEvent, WebhookTestEvent, UnknownWebhookEvent
]


@dataclass(frozen=True)
class WebhookDelivery:
    delivery_id: str
    timestamp: datetime
    matched_secret_index: int
    signature_count: int


@dataclass(frozen=True)
class VerifiedWebhook:
    event: EpostixWebhookEvent
    delivery: WebhookDelivery


def is_email_delivery_event(event: EpostixWebhookEvent) -> bool:
    return event.type in DELIVERY_EVENT_TYPES


def is_inbound_email_event(event: EpostixWebhookEvent) -> bool:
    return event.type == WebhookEventType.EMAIL_RECEIVED.value


def is_webhook_test_event(event: EpostixWebhookEvent) -> bool:
    return event.type == WebhookEventType.WEBHOOK_TEST.value


def is_unknown_webhook_event(event: EpostixWebhookEvent) -> bool:
    return isinstance(event, UnknownWebhookEvent)


def _created_at(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def decode_event(payload: bytes | str) -> EpostixWebhookEvent:
    raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    parsed = json.loads(raw)

    if not isinstance(parsed, dict):
        raise ValueError("a webhook event must be a JSON object")

    identifier = str(parsed.get("id", ""))
    event_type = str(parsed.get("type", ""))
    created_at = _created_at(parsed.get("created_at"))
    data = parsed.get("data")
    fields: dict[str, JsonValue] = data if isinstance(data, dict) else {}

    if event_type in DELIVERY_EVENT_TYPES:
        return EmailDeliveryEvent(
            id=identifier,
            type=event_type,
            created_at=created_at,
            email_id=str(fields.get("email_id", "")),
            data=fields,
        )

    if event_type == WebhookEventType.EMAIL_RECEIVED.value:
        return InboundEmailEvent(
            id=identifier,
            type=event_type,
            created_at=created_at,
            inbound_id=str(fields.get("inbound_id", "")),
            data=fields,
        )

    if event_type == WebhookEventType.WEBHOOK_TEST.value:
        return WebhookTestEvent(
            id=identifier,
            type=event_type,
            created_at=created_at,
            message=str(fields.get("message", "")),
            data=fields,
        )

    return UnknownWebhookEvent(
        id=identifier,
        type=event_type,
        created_at=created_at,
        data=fields,
        raw_body=raw,
    )


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    signed = f"{timestamp}.".encode("utf-8") + payload

    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def _header_of(headers: Mapping[str, Any], name: str) -> str | None:
    lower = name.lower()

    for key, value in headers.items():
        if key.lower() != lower:
            continue

        if isinstance(value, (list, tuple)):
            return str(value[0]) if value else None

        return None if value is None else str(value)

    return None


def _parse_signature_header(raw: str) -> tuple[int, list[str]] | None:
    signatures: list[str] = []
    timestamp: int | None = None

    for part in raw.split(","):
        separator = part.find("=")
        if separator < 0:
            return None

        key = part[:separator].strip()
        value = part[separator + 1 :].strip()

        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None
            continue

        if key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return None

    return timestamp, signatures


class EpostixWebhookVerifier:
    def __init__(
        self,
        secrets: Sequence[str],
        *,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not secrets:
            raise EpostixWebhookVerificationError(
                "NoMatchingSignature", "at least one signing secret is required"
            )

        self._secrets = list(secrets)
        self._tolerance_seconds = tolerance_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @classmethod
    def from_rotation(cls, current_secret: str, previous_secret: str) -> EpostixWebhookVerifier:
        return cls([current_secret, previous_secret])

    @staticmethod
    def decode_without_verifying(payload: bytes | str) -> EpostixWebhookEvent:
        return decode_event(payload)

    def verify(self, payload: bytes | str, headers: Mapping[str, Any]) -> WebhookDelivery:
        raw = payload if isinstance(payload, bytes) else payload.encode("utf-8")

        signature_header = _header_of(headers, WEBHOOK_HEADER_SIGNATURE)
        if not signature_header:
            raise EpostixWebhookVerificationError(
                "MissingSignatureHeader", f"{WEBHOOK_HEADER_SIGNATURE} is missing"
            )

        timestamp_header = _header_of(headers, WEBHOOK_HEADER_TIMESTAMP)
        if not timestamp_header:
            raise EpostixWebhookVerificationError(
                "MissingTimestampHeader", f"{WEBHOOK_HEADER_TIMESTAMP} is missing"
            )

        parsed = _parse_signature_header(signature_header)
        if parsed is None:
            raise EpostixWebhookVerificationError(
                "MalformedSignatureHeader",
                f"{WEBHOOK_HEADER_SIGNATURE} is not in the form t=...,v1=...",
            )

        timestamp, signatures = parsed

        if str(timestamp) != timestamp_header.strip():
            raise EpostixWebhookVerificationError(
                "MalformedSignatureHeader",
                f"{WEBHOOK_HEADER_TIMESTAMP} and the signed timestamp disagree",
            )

        now = int(self._clock().timestamp())
        if abs(now - timestamp) > self._tolerance_seconds:
            raise EpostixWebhookVerificationError(
                "TimestampOutOfTolerance",
                f"the signature timestamp is outside the {self._tolerance_seconds}"
                " second tolerance",
            )

        matched = -1

        for index, secret in enumerate(self._secrets):
            expected = sign(raw, secret, timestamp)

            for candidate in signatures:
                if hmac.compare_digest(expected, candidate) and matched < 0:
                    matched = index

        if matched < 0:
            raise EpostixWebhookVerificationError(
                "NoMatchingSignature", "no configured secret produced a matching signature"
            )

        return WebhookDelivery(
            delivery_id=_header_of(headers, WEBHOOK_HEADER_ID) or "",
            timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
            matched_secret_index=matched,
            signature_count=len(signatures),
        )

    def verify_and_decode(
        self, payload: bytes | str, headers: Mapping[str, Any]
    ) -> VerifiedWebhook:
        delivery = self.verify(payload, headers)

        try:
            event = decode_event(payload)
        except ValueError as error:
            raise EpostixWebhookVerificationError(
                "MalformedPayload", "the signature verified but the payload is not valid JSON"
            ) from error

        return VerifiedWebhook(event=event, delivery=delivery)

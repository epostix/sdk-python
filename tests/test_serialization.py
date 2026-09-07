from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from conftest import codec_for, load_fixture

from epostix import NOT_GIVEN, Mailbox, addr, named
from epostix._generated.codecs import (
    CODEC_ATTACHMENT,
    CODEC_EMAIL_CREATE,
    CODEC_EMAIL_CREATE_ATTACHMENTS_ITEM,
    CODEC_EMAIL_UPDATE,
)
from epostix.attachments import from_remote_url, from_uploaded_id
from epostix.models import Attachment, EmailCreate, EmailUpdate

FIXTURE = load_fixture("serialization")


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: str(case["name"]))
def test_wire_body_matches_the_shared_corpus(case: dict[str, Any]) -> None:
    codec = codec_for(case["type"])

    wire = codec.encode(codec.decode(case["input"]))

    for key in case.get("absentKeys", []):
        assert key not in wire, f"{key} must be absent from the wire body"

    assert wire == case["expected"]


def test_the_corpus_is_not_empty() -> None:
    assert len(FIXTURE["cases"]) > 0


def _minimal() -> EmailCreate:
    return EmailCreate(from_=addr("a@example.com"), to="b@example.com", subject="s")


def test_recipients_always_serialize_as_an_array_of_plain_strings() -> None:
    wire = CODEC_EMAIL_CREATE.encode(_minimal())

    assert wire["to"] == ["b@example.com"]


def test_a_recipient_list_stays_a_list() -> None:
    body = EmailCreate(
        from_=addr("a@example.com"), to=["one@example.com", "two@example.com"], subject="s"
    )

    assert CODEC_EMAIL_CREATE.encode(body)["to"] == ["one@example.com", "two@example.com"]


def test_a_mailbox_without_a_display_name_emits_the_narrowest_form() -> None:
    assert CODEC_EMAIL_CREATE.encode(_minimal())["from"] == "a@example.com"


def test_a_mailbox_with_a_display_name_emits_an_object() -> None:
    body = EmailCreate(from_=named("Billing", "a@example.com"), to="b@example.com", subject="s")

    assert CODEC_EMAIL_CREATE.encode(body)["from"] == {
        "email": "a@example.com",
        "name": "Billing",
    }


def test_a_bare_string_sender_is_passed_through() -> None:
    body = EmailCreate(from_="a@example.com", to="b@example.com", subject="s")

    assert CODEC_EMAIL_CREATE.encode(body)["from"] == "a@example.com"


def test_false_reaches_the_wire() -> None:
    body = _minimal()
    body.allow_duplicate = False

    wire = CODEC_EMAIL_CREATE.encode(body)

    assert "allow_duplicate" in wire
    assert wire["allow_duplicate"] is False


def test_an_empty_string_reaches_the_wire() -> None:
    body = _minimal()
    body.text = ""

    wire = CODEC_EMAIL_CREATE.encode(body)

    assert "text" in wire
    assert wire["text"] == ""


def test_an_unset_optional_is_absent() -> None:
    wire = CODEC_EMAIL_CREATE.encode(_minimal())

    for key in ("text", "html", "cc", "bcc", "allow_duplicate", "scheduled_at"):
        assert key not in wire


def test_a_default_the_caller_did_not_set_is_never_sent() -> None:
    wire = CODEC_ATTACHMENT.encode(
        Attachment(filename="r.pdf", content="AAEC", content_type="application/pdf")
    )

    assert "content_disposition" not in wire


def test_an_unset_scheduled_at_omits_the_key_entirely() -> None:
    assert CODEC_EMAIL_UPDATE.encode(EmailUpdate()) == {}


def test_an_explicit_null_scheduled_at_sends_now() -> None:
    body = EmailUpdate(scheduled_at=None)

    assert CODEC_EMAIL_UPDATE.encode(body) == {"scheduled_at": None}


def test_a_scheduled_at_value_reschedules() -> None:
    at = datetime(2026, 9, 7, 10, 0, 0, tzinfo=timezone.utc)
    body = EmailUpdate(scheduled_at=at)

    assert CODEC_EMAIL_UPDATE.encode(body) == {"scheduled_at": "2026-09-07T10:00:00Z"}


def test_the_three_scheduled_at_states_are_distinguishable() -> None:
    assert EmailUpdate().scheduled_at is NOT_GIVEN
    assert EmailUpdate(scheduled_at=None).scheduled_at is None
    assert isinstance(EmailUpdate(scheduled_at=datetime.now(timezone.utc)).scheduled_at, datetime)


def test_an_explicit_offset_is_preserved_not_localised() -> None:
    body = EmailCreate(from_="a@example.com", to="b@example.com", subject="s")
    body.scheduled_at = datetime.fromisoformat("2026-09-07T10:00:00+02:00")

    assert CODEC_EMAIL_CREATE.encode(body)["scheduled_at"] == "2026-09-07T10:00:00+02:00"


def test_a_reference_attachment_carries_only_its_id() -> None:
    wire = CODEC_EMAIL_CREATE_ATTACHMENTS_ITEM.encode(from_uploaded_id("att_9"))

    assert wire == {"attachment_id": "att_9"}


def test_a_remote_attachment_never_carries_inline_content() -> None:
    wire = CODEC_EMAIL_CREATE_ATTACHMENTS_ITEM.encode(
        from_remote_url("r.pdf", "https://example.com/r.pdf")
    )

    assert wire == {"filename": "r.pdf", "content_url": "https://example.com/r.pdf"}
    assert "content" not in wire


def test_recipient_elements_are_plain_strings_not_mailboxes() -> None:
    body = EmailCreate(from_=addr("a@example.com"), to=["b@example.com"], subject="s")
    body.cc = "c@example.com"

    wire = CODEC_EMAIL_CREATE.encode(body)

    assert wire["to"] == ["b@example.com"]
    assert wire["cc"] == ["c@example.com"]
    assert all(isinstance(item, str) for item in wire["to"])


def test_a_mailbox_renders_as_a_display_string() -> None:
    assert str(Mailbox(email="a@example.com")) == "a@example.com"
    assert str(named("Billing", "a@example.com")) == "Billing <a@example.com>"

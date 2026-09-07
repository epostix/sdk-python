from __future__ import annotations

import base64
import json

import pytest
from conftest import Clients, Recorder, Reply

from epostix import EpostixConfigurationError, EpostixNotFoundError, RawContent
from epostix.attachments import ATTACHMENT_DECODED_LIMIT_BYTES, EpostixAttachmentTooLargeError

RAW_MESSAGE = b"From: a@example.com\r\nSubject: hi\r\n\r\nbody\r\n"


def an_upload() -> dict[str, object]:
    return {
        "id": "att_1",
        "filename": "r.pdf",
        "content_type": "application/pdf",
        "size": 3,
        "expires_at": "2026-09-07T10:00:00Z",
    }


def test_upload_attachment_frames_the_content_as_base64(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    client = clients.sync(recorder)

    result = client.attachments.upload_attachment("r.pdf", b"\x00\x01\x02", "application/pdf")

    assert result.id == "att_1"
    assert json.loads(recorder.requests[0].content) == {
        "filename": "r.pdf",
        "content": base64.b64encode(b"\x00\x01\x02").decode("ascii"),
        "content_type": "application/pdf",
    }


def test_upload_attachment_posts_to_the_generated_operation(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    client = clients.sync(recorder)

    client.attachments.upload_attachment("r.pdf", b"\x00", "application/pdf")

    request = recorder.requests[0]
    assert request.method == "POST"
    assert str(request.url).endswith("/attachments")


def test_upload_attachment_projects_the_size_before_any_network_call(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    client = clients.sync(recorder)

    with pytest.raises(EpostixAttachmentTooLargeError):
        client.attachments.upload_attachment(
            "huge.bin", b"x" * (ATTACHMENT_DECODED_LIMIT_BYTES + 1), "application/octet-stream"
        )

    assert recorder.count == 0


def test_upload_attachment_never_carries_an_idempotency_key(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    client = clients.sync(recorder)

    client.attachments.upload_attachment("r.pdf", b"\x00", "application/pdf")

    assert "idempotency-key" not in recorder.requests[0].headers


async def test_the_async_upload_sends_the_same_bytes(clients: Clients) -> None:
    synchronous = Recorder([Reply(body=an_upload())])
    clients.sync(synchronous).attachments.upload_attachment("r.pdf", b"\x00\x01", "application/pdf")

    asynchronous = Recorder([Reply(body=an_upload())])
    client = clients.asynchronous(asynchronous)
    await client.attachments.upload_attachment("r.pdf", b"\x00\x01", "application/pdf")
    await client.aclose()

    assert synchronous.requests[0].content == asynchronous.requests[0].content


def test_the_raw_message_streams_with_explicit_close(clients: Clients) -> None:
    recorder = Recorder(
        [Reply(text=RAW_MESSAGE.decode(), headers={"content-type": "message/rfc822"})]
    )
    client = clients.sync(recorder)

    raw = client.inbound.get_inbound_email_raw("inb_1")

    assert isinstance(raw, RawContent)
    assert raw.content_type == "message/rfc822"
    assert b"".join(raw.iter_bytes()) == RAW_MESSAGE

    raw.close()


def test_the_raw_message_works_as_a_context_manager(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.sync(recorder)

    with client.inbound.get_inbound_email_raw("inb_1") as raw:
        collected = b"".join(raw.iter_bytes())

    assert collected == RAW_MESSAGE


def test_a_raw_fetch_maps_an_error_status_to_a_typed_error(clients: Clients) -> None:
    recorder = Recorder(
        [Reply(status=404, body={"type": "not_found", "message": "gone", "request_id": "req_1"})]
    )
    client = clients.sync(recorder)

    with pytest.raises(EpostixNotFoundError):
        client.inbound.get_inbound_email_raw("inb_1")


def test_raw_bytes_requires_a_cap(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.sync(recorder)

    with pytest.raises(EpostixConfigurationError):
        client.inbound.get_inbound_email_raw_bytes("inb_1", 0)

    assert recorder.count == 0


def test_raw_bytes_returns_the_message_within_the_cap(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.sync(recorder)

    assert client.inbound.get_inbound_email_raw_bytes("inb_1", 4096) == RAW_MESSAGE


def test_raw_bytes_refuses_a_message_larger_than_the_cap(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.sync(recorder)

    with pytest.raises(EpostixConfigurationError) as raised:
        client.inbound.get_inbound_email_raw_bytes("inb_1", 4)

    assert "4 byte cap" in str(raised.value)


async def test_the_async_raw_path_matches(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.asynchronous(recorder)

    assert await client.inbound.get_inbound_email_raw_bytes("inb_1", 4096) == RAW_MESSAGE

    await client.aclose()


async def test_the_async_raw_stream_closes_explicitly(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.asynchronous(recorder)

    raw = await client.inbound.get_inbound_email_raw("inb_1")
    collected = b"".join([chunk async for chunk in raw.aiter_bytes()])
    await raw.aclose()

    assert collected == RAW_MESSAGE

    await client.aclose()


async def test_the_async_raw_cap_is_required(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.asynchronous(recorder)

    with pytest.raises(EpostixConfigurationError):
        await client.inbound.get_inbound_email_raw_bytes("inb_1", -1)

    assert recorder.count == 0

    await client.aclose()


def test_a_raw_fetch_asks_for_any_content_type(clients: Clients) -> None:
    recorder = Recorder([Reply(text=RAW_MESSAGE.decode())])
    client = clients.sync(recorder)

    client.inbound.get_inbound_email_raw("inb_1").close()

    assert recorder.requests[0].headers["accept"] == "*/*"
    assert "idempotency-key" not in recorder.requests[0].headers


async def test_the_async_upload_also_projects_the_size_before_any_network_call(
    clients: Clients,
) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    client = clients.asynchronous(recorder)

    with pytest.raises(EpostixAttachmentTooLargeError):
        await client.attachments.upload_attachment(
            "huge.bin", b"x" * (ATTACHMENT_DECODED_LIMIT_BYTES + 1), "application/octet-stream"
        )

    assert recorder.count == 0

    await client.aclose()


def test_both_clients_reach_every_overridden_operation(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_upload())])
    synchronous = clients.sync(recorder)
    asynchronous = clients.asynchronous(recorder)

    for client in (synchronous, asynchronous):
        assert callable(client.attachments.upload_attachment)
        assert callable(client.inbound.get_inbound_email_raw)
        assert callable(client.inbound.get_inbound_email_raw_bytes)

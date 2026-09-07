from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from conftest import Clients, Recorder, Reply, pages

from epostix import addr, named, response_metadata_of
from epostix.attachments import from_bytes, from_remote_url, from_uploaded_id
from epostix.models import EmailCreate, EmailUpdate

IGNORED_HEADERS = {"idempotency-key", "host", "connection", "accept-encoding"}


def wire_of(request: httpx.Request) -> dict[str, Any]:
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in IGNORED_HEADERS
        },
        "body": request.content,
    }


def a_rich_send() -> EmailCreate:
    body = EmailCreate(
        from_=named("Billing", "billing@example.com"),
        to=["one@example.com", "two@example.com"],
        subject="Your receipt",
    )
    body.cc = "cc@example.com"
    body.reply_to = addr("reply@example.com")
    body.text = ""
    body.allow_duplicate = False
    body.scheduled_at = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
    body.metadata = {"order": 4821, "vip": True, "nested": {"k": None}}
    body.tags = ["receipt"]
    body.attachments = [
        from_bytes("r.pdf", b"\x00\x01\x02", "application/pdf"),
        from_uploaded_id("att_9"),
        from_remote_url("remote.pdf", "https://example.com/r.pdf"),
    ]

    return body


def an_email() -> dict[str, object]:
    return {
        "id": "eml_1",
        "status": "queued",
        "created_at": "2026-09-06T10:00:00Z",
        "updated_at": "2026-09-06T10:00:00Z",
        "to": ["one@example.com"],
    }


async def test_both_clients_put_the_same_bytes_on_the_wire(clients: Clients) -> None:
    body = a_rich_send()

    synchronous = Recorder([Reply(body=an_email())])
    clients.sync(synchronous, automatic_idempotency_keys=False).emails.send_email(body)

    asynchronous = Recorder([Reply(body=an_email())])
    client = clients.asynchronous(asynchronous, automatic_idempotency_keys=False)
    await client.emails.send_email(body)
    await client.aclose()

    assert wire_of(synchronous.requests[0]) == wire_of(asynchronous.requests[0])


async def test_both_clients_build_the_same_query_string(clients: Clients) -> None:
    synchronous = Recorder([Reply(body={"data": [], "has_more": False})])
    clients.sync(synchronous).emails.list_emails(
        limit=25,
        status="delivered",
        tags=["a", "b"],
        created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    asynchronous = Recorder([Reply(body={"data": [], "has_more": False})])
    client = clients.asynchronous(asynchronous)
    await client.emails.list_emails(
        limit=25,
        status="delivered",
        tags=["a", "b"],
        created_after=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    await client.aclose()

    assert wire_of(synchronous.requests[0]) == wire_of(asynchronous.requests[0])


async def test_both_clients_decode_the_same_model(clients: Clients) -> None:
    synchronous = Recorder([Reply(body=an_email(), headers={"Idempotent-Replayed": "true"})])
    from_sync = clients.sync(synchronous).emails.get_email("eml_1")

    asynchronous = Recorder([Reply(body=an_email(), headers={"Idempotent-Replayed": "true"})])
    client = clients.asynchronous(asynchronous)
    from_async = await client.emails.get_email("eml_1")
    await client.aclose()

    assert from_sync == from_async

    sync_metadata = response_metadata_of(from_sync)
    async_metadata = response_metadata_of(from_async)
    assert sync_metadata is not None and async_metadata is not None
    assert sync_metadata.idempotent_replayed == async_metadata.idempotent_replayed is True


async def test_both_clients_walk_pages_identically(clients: Clients) -> None:
    bodies = [
        {"data": [{"id": "e1"}, {"id": "e2"}], "has_more": True, "next_cursor": "e2"},
        {"data": [{"id": "e3"}], "has_more": False},
    ]

    synchronous = Recorder(pages(bodies))
    sync_ids = [
        item.id for item in clients.sync(synchronous).emails.list_emails_iterate(status="delivered")
    ]

    asynchronous = Recorder(pages(bodies))
    client = clients.asynchronous(asynchronous)
    async_ids = [
        item.id async for item in client.emails.list_emails_iterate(status="delivered")
    ]
    await client.aclose()

    assert sync_ids == async_ids == ["e1", "e2", "e3"]
    assert [wire_of(request) for request in synchronous.requests] == [
        wire_of(request) for request in asynchronous.requests
    ]


async def test_both_clients_send_the_same_tristate_update(clients: Clients) -> None:
    for update in (EmailUpdate(), EmailUpdate(scheduled_at=None)):
        synchronous = Recorder([Reply(body=an_email())])
        clients.sync(synchronous).emails.update_email("eml_1", update)

        asynchronous = Recorder([Reply(body=an_email())])
        client = clients.asynchronous(asynchronous)
        await client.emails.update_email("eml_1", update)
        await client.aclose()

        assert wire_of(synchronous.requests[0]) == wire_of(asynchronous.requests[0])

from __future__ import annotations

import inspect
from typing import Any

import pytest
from conftest import Clients, Recorder, Reply

from epostix._generated.operations import OPERATIONS

IDEMPOTENT_METHODS = {
    "bulk_create_suppressions",
    "create_broadcast",
    "create_suppression",
    "create_template",
    "create_template_version",
    "send_email",
    "send_email_batch",
}

OVERRIDDEN = {"uploadAttachment", "getInboundEmailRaw"}


def resources_of(client: Any) -> list[Any]:
    found: list[Any] = []

    for name in ("analytics", "api_keys", "attachments", "broadcasts", "contacts", "domains",
                 "emails", "events", "inbound", "suppressions", "tags", "templates", "webhooks"):
        resource = getattr(client, name)
        found.append(resource)

        for child in ("versions", "logs"):
            if hasattr(resource, child):
                found.append(getattr(resource, child))

    return found


def methods_of(resource: Any) -> dict[str, Any]:
    return {
        name: getattr(resource, name)
        for name in dir(resource)
        if not name.startswith("_") and callable(getattr(resource, name))
    }


def test_the_operation_table_carries_every_operation() -> None:
    assert len(OPERATIONS) == 79
    assert sum(1 for spec in OPERATIONS.values() if spec.idempotent) == 7


def test_an_overridden_operation_keeps_its_spec_and_a_hand_written_method(
    clients: Clients,
) -> None:
    client = clients.sync(Recorder([Reply(body={})]))

    for identifier in OVERRIDDEN:
        assert identifier in OPERATIONS

    assert callable(client.attachments.upload_attachment)
    assert callable(client.inbound.get_inbound_email_raw)
    assert callable(client.inbound.get_inbound_email_raw_bytes)


def test_the_generated_resources_emit_no_method_for_an_overridden_operation() -> None:
    from epostix._generated.resources.attachments import AttachmentsResource
    from epostix._generated.resources.inbound import InboundResource

    assert not hasattr(AttachmentsResource, "upload_attachment")
    assert not hasattr(InboundResource, "get_inbound_email_raw")


def test_only_deduplicated_operations_accept_an_idempotency_key(clients: Clients) -> None:
    client = clients.sync(Recorder([Reply(body={})]))
    accepting: set[str] = set()

    for resource in resources_of(client):
        for name, method in methods_of(resource).items():
            if "idempotency_key" in inspect.signature(method).parameters:
                accepting.add(name)

    assert accepting == IDEMPOTENT_METHODS


def test_passing_a_key_to_an_excluded_operation_is_a_type_error(clients: Clients) -> None:
    recorder = Recorder([Reply(body={"id": "eml_1", "status": "cancelled"})])
    client = clients.sync(recorder)

    with pytest.raises(TypeError):
        client.emails.cancel_email("eml_1", idempotency_key="k")  # type: ignore[call-arg]

    assert recorder.count == 0


def test_every_operation_is_reachable_from_a_resource(clients: Clients) -> None:
    client = clients.sync(Recorder([Reply(body={})]))
    total = 0

    for resource in resources_of(client):
        total += len(methods_of(resource))

    iterators = sum(1 for spec in OPERATIONS.values() if spec.id.startswith(("list", "getAPIKey")))

    assert total == len(OPERATIONS) - len(OVERRIDDEN) + 15 + 3
    assert iterators >= 15


def test_sync_and_async_resources_expose_the_same_method_names(clients: Clients) -> None:
    recorder = Recorder([Reply(body={})])
    synchronous = clients.sync(recorder)
    asynchronous = clients.asynchronous(recorder)

    for name in ("emails", "templates", "webhooks", "contacts", "api_keys"):
        assert set(methods_of(getattr(synchronous, name))) == set(
            methods_of(getattr(asynchronous, name))
        )


def test_nested_resources_are_reachable(clients: Clients) -> None:
    client = clients.sync(Recorder([Reply(body={})]))

    assert client.templates.versions is not None
    assert client.webhooks.logs is not None

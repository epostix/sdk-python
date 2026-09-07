from __future__ import annotations

import json
import re

import httpx
import pytest
from conftest import API_KEY, Clients, Recorder, Reply

from epostix import (
    EpostixAPIError,
    EpostixConfigurationError,
    EpostixOutcomeUnknownError,
    EpostixServerError,
    EpostixTransportError,
    Epostix,
    RetryPolicy,
    addr,
)
from epostix.models import EmailCreate

AUTO_KEY = re.compile(r"^epx_auto_[0-9a-f]{32}$")

FAST = RetryPolicy(backoff_base=0.001, backoff_maximum=0.001)


def a_send() -> EmailCreate:
    return EmailCreate(from_=addr("a@example.com"), to="b@example.com", subject="s")


def an_email() -> dict[str, object]:
    return {"id": "eml_1", "status": "queued", "created_at": "2026-09-06T10:00:00Z"}


def test_the_authorization_header_carries_the_bare_key(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    clients.sync(recorder).emails.get_email("eml_1")

    headers = recorder.requests[0].headers
    assert headers["authorization"] == f"Bearer {API_KEY}"
    assert headers["user-agent"].startswith("epostix-python/")
    assert headers["accept"] == "application/json"


def test_a_body_is_sent_as_json(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    clients.sync(recorder).emails.send_email(a_send())

    request = recorder.requests[0]
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "from": "a@example.com",
        "to": ["b@example.com"],
        "subject": "s",
    }


def test_path_parameters_are_escaped(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    clients.sync(recorder).emails.get_email("eml/1")

    assert str(recorder.requests[0].url).endswith("/emails/eml%2F1")


def test_an_empty_path_parameter_is_refused(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])

    with pytest.raises(EpostixConfigurationError):
        clients.sync(recorder).emails.get_email("")

    assert recorder.count == 0


def test_an_idempotent_operation_gets_an_automatic_key(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    clients.sync(recorder).emails.send_email(a_send())

    key = recorder.requests[0].headers["idempotency-key"]
    assert AUTO_KEY.match(key)


def test_automatic_keys_are_not_derived_from_the_message(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email()), Reply(body=an_email())])
    client = clients.sync(recorder)

    client.emails.send_email(a_send())
    client.emails.send_email(a_send())

    first = recorder.requests[0].headers["idempotency-key"]
    second = recorder.requests[1].headers["idempotency-key"]

    assert first != second


def test_automatic_keys_can_be_switched_off(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    client = clients.sync(recorder, automatic_idempotency_keys=False)

    client.emails.send_email(a_send())

    assert "idempotency-key" not in recorder.requests[0].headers


def test_a_supplied_key_is_used_verbatim(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])
    clients.sync(recorder).emails.send_email(a_send(), idempotency_key="order-4821")

    assert recorder.requests[0].headers["idempotency-key"] == "order-4821"


def test_an_over_long_key_is_refused(clients: Clients) -> None:
    recorder = Recorder([Reply(body=an_email())])

    with pytest.raises(EpostixConfigurationError):
        clients.sync(recorder).emails.send_email(a_send(), idempotency_key="k" * 256)

    assert recorder.count == 0


def test_a_non_idempotent_operation_never_carries_a_key(clients: Clients) -> None:
    recorder = Recorder([Reply(body={"id": "eml_1", "status": "cancelled"})])
    clients.sync(recorder).emails.cancel_email("eml_1")

    assert "idempotency-key" not in recorder.requests[0].headers


def test_the_body_is_replayed_byte_identically_across_attempts(clients: Clients) -> None:
    recorder = Recorder(
        [
            Reply(status=503, body={"type": "service_unavailable", "message": "down"}),
            Reply(status=503, body={"type": "service_unavailable", "message": "down"}),
            Reply(body=an_email()),
        ]
    )
    client = clients.sync(recorder, retry_policy=FAST)

    client.emails.send_email(a_send())

    assert recorder.count == 3
    bodies = {request.content for request in recorder.requests}
    keys = {request.headers["idempotency-key"] for request in recorder.requests}

    assert len(bodies) == 1
    assert len(keys) == 1


def test_the_attempt_budget_is_two_retries(clients: Clients) -> None:
    recorder = Recorder([Reply(status=500, body={"type": "internal_error", "message": "boom"})])
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixServerError) as raised:
        client.emails.get_email("eml_1")

    assert recorder.count == 3
    assert raised.value.response_metadata.attempts == 3
    assert raised.value.response_metadata.retries_exhausted_reason == "AttemptBudgetExhausted"
    assert len(raised.value.response_metadata.attempt_records) == 3


def test_a_lost_response_on_a_mutation_is_never_reported_as_success(clients: Clients) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    http = httpx.Client(transport=httpx.MockTransport(refuse))
    client = Epostix(
        API_KEY, base_url="https://api.epostix.test/v1", http_client=http, retry_policy=FAST
    )

    with pytest.raises(EpostixOutcomeUnknownError) as raised:
        client.emails.send_email(a_send())

    assert raised.value.replayable_with_key is True
    assert raised.value.idempotency_key is not None
    assert AUTO_KEY.match(raised.value.idempotency_key)
    assert raised.value.operation == "sendEmail"

    http.close()


def test_a_lost_response_on_a_read_stays_a_transport_error(clients: Clients) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    http = httpx.Client(transport=httpx.MockTransport(refuse))
    client = Epostix(
        API_KEY, base_url="https://api.epostix.test/v1", http_client=http, retry_policy=FAST
    )

    with pytest.raises(EpostixTransportError):
        client.emails.get_email("eml_1")

    http.close()


def test_an_empty_response_decodes_to_none(clients: Clients) -> None:
    recorder = Recorder([Reply(status=204, body=None)])
    client = clients.sync(recorder)

    assert client.contacts.assign_contact_tag("con_1", "tag_1") is None


def test_a_non_json_error_body_is_bounded_and_kept(clients: Clients) -> None:
    recorder = Recorder([Reply(status=502, text="<html>" + "x" * 20000 + "</html>")])
    client = clients.sync(recorder)

    with pytest.raises(EpostixAPIError) as raised:
        client.emails.get_email("eml_1")

    error = raised.value
    assert error.type == ""
    assert error.is_known_type is False
    assert error.raw_body_snippet is not None
    assert len(error.raw_body_snippet) == 8192
    assert error.raw_body_truncated is True


def test_query_values_are_formatted_for_the_wire(clients: Clients) -> None:
    recorder = Recorder([Reply(body={"data": [], "has_more": False})])
    client = clients.sync(recorder)

    client.emails.list_emails(limit=25, status="delivered", tags=["a", "b"])

    query = recorder.query_of(0)
    assert query["limit"] == ["25"]
    assert query["status"] == ["delivered"]
    assert query["tags"] == ["a", "b"]


def test_unset_query_values_are_omitted(clients: Clients) -> None:
    recorder = Recorder([Reply(body={"data": [], "has_more": False})])
    client = clients.sync(recorder)

    client.emails.list_emails(limit=25)

    assert recorder.query_of(0) == {"limit": ["25"]}


def test_an_injected_http_client_is_never_closed() -> None:
    http = httpx.Client(transport=httpx.MockTransport(Recorder([Reply(body=an_email())])))
    client = Epostix(API_KEY, base_url="https://api.epostix.test/v1", http_client=http)

    client.close()

    assert http.is_closed is False
    http.close()


def test_a_client_owned_transport_is_closed() -> None:
    client = Epostix(API_KEY, base_url="https://api.epostix.test/v1")
    http = client._transport.http_client

    with client:
        pass

    assert http.is_closed is True


def test_with_api_key_shares_the_transport_and_nothing_else() -> None:
    http = httpx.Client(transport=httpx.MockTransport(Recorder([Reply(body=an_email())])))
    client = Epostix(API_KEY, base_url="https://api.epostix.test/v1", http_client=http)
    derived = client.with_api_key("tix_live_other")

    assert derived._transport.http_client is http
    assert derived.key_environment == "live"
    assert client.key_environment == "test"

    derived.close()
    assert http.is_closed is False

    http.close()


def test_an_empty_api_key_is_refused() -> None:
    with pytest.raises(EpostixConfigurationError):
        Epostix("   ")


def test_a_bearer_prefixed_key_is_refused() -> None:
    with pytest.raises(EpostixConfigurationError):
        Epostix("Bearer tix_test_abc")


def test_a_malformed_base_url_is_refused() -> None:
    with pytest.raises(EpostixConfigurationError):
        Epostix(API_KEY, base_url="not-a-url")

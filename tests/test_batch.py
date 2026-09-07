from __future__ import annotations

from typing import Any

from conftest import Clients, Recorder, Reply

from epostix.batch import AcceptedOutcome, RejectedOutcome, UnresolvedOutcome, normalize_batch
from epostix.models import EmailBatchRequest, EmailCreate
from epostix import addr


def a_send() -> EmailCreate:
    return EmailCreate(from_=addr("a@example.com"), to="b@example.com", subject="s")


def send_batch(clients: Clients, body: dict[str, Any], count: int) -> Any:
    recorder = Recorder([Reply(body=body)])
    client = clients.sync(recorder)
    response = client.emails.send_email_batch(EmailBatchRequest(emails=[a_send() for _ in range(count)]))

    return normalize_batch(response, count)


def test_every_input_index_gets_exactly_one_outcome(clients: Clients) -> None:
    result = send_batch(
        clients,
        {
            "batch_id": "bat_1",
            "results": [
                {"index": 0, "id": "eml_1", "status": "queued"},
                {"index": 1, "id": "eml_2", "status": "queued"},
                {"index": 2, "id": "eml_3", "status": "queued"},
            ],
        },
        3,
    )

    assert len(result.outcomes) == 3
    assert [outcome.index for outcome in result.outcomes] == [0, 1, 2]
    assert result.all_accepted is True
    assert result.accepted_email_ids == ["eml_1", "eml_2", "eml_3"]
    assert result.batch_id == "bat_1"


def test_an_index_the_server_never_mentioned_is_unresolved(clients: Clients) -> None:
    result = send_batch(
        clients,
        {"batch_id": "bat_1", "results": [{"index": 0, "id": "eml_1", "status": "queued"}]},
        3,
    )

    assert len(result.outcomes) == 3
    assert isinstance(result.outcomes[1], UnresolvedOutcome)
    assert result.outcomes[1].reason == "NotReported"
    assert result.has_unresolved is True
    assert result.all_accepted is False


def test_a_failed_result_without_an_error_is_unresolved_not_rejected(clients: Clients) -> None:
    result = send_batch(
        clients,
        {"batch_id": "bat_1", "results": [{"index": 0, "status": "failed"}]},
        1,
    )

    assert isinstance(result.outcomes[0], UnresolvedOutcome)
    assert result.outcomes[0].reason == "StatusFailedWithoutError"


def test_a_result_error_is_reported_as_a_rejection(clients: Clients) -> None:
    result = send_batch(
        clients,
        {
            "batch_id": "bat_1",
            "results": [
                {
                    "index": 0,
                    "status": "failed",
                    "error": {
                        "status": 422,
                        "type": "invalid_from_address",
                        "message": "bad sender",
                        "request_id": "req_1",
                    },
                }
            ],
        },
        1,
    )

    outcome = result.outcomes[0]
    assert isinstance(outcome, RejectedOutcome)
    assert outcome.error_type == "invalid_from_address"
    assert outcome.message == "bad sender"
    assert result.all_accepted is False


def test_a_permissive_mode_error_list_fills_the_remaining_indexes(clients: Clients) -> None:
    result = send_batch(
        clients,
        {
            "batch_id": "bat_1",
            "results": [{"index": 0, "id": "eml_1", "status": "queued"}],
            "errors": [{"index": 1, "code": "validation_error", "message": "no subject"}],
        },
        2,
    )

    assert isinstance(result.outcomes[0], AcceptedOutcome)
    assert isinstance(result.outcomes[1], RejectedOutcome)
    assert result.outcomes[1].error_type == "validation_error"
    assert result.has_unresolved is False


def test_the_unresolved_bucket_exists_even_when_everything_was_accepted(
    clients: Clients,
) -> None:
    result = send_batch(
        clients,
        {"batch_id": "bat_1", "results": [{"index": 0, "id": "eml_1", "status": "queued"}]},
        1,
    )

    assert result.unresolved == []
    assert result.has_unresolved is False
    assert result.all_accepted is True


def test_a_missing_send_state_falls_back_to_pending(clients: Clients) -> None:
    result = send_batch(
        clients,
        {"batch_id": "bat_1", "results": [{"index": 0, "id": "eml_1", "status": "queued"}]},
        1,
    )

    outcome = result.outcomes[0]
    assert isinstance(outcome, AcceptedOutcome)
    assert outcome.status == "queued"

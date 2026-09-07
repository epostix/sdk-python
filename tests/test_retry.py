from __future__ import annotations

from typing import Any

import pytest
from conftest import Clients, Recorder, Reply, replies

from epostix import (
    DEFAULT_RETRY_POLICY,
    DISABLED_RETRY_POLICY,
    EpostixIdempotencyConflictError,
    EpostixOutcomeUnknownError,
    EpostixQuotaExceededError,
    EpostixRateLimitError,
    EpostixServerError,
    RetryClass,
    RetryPolicy,
    addr,
)
from epostix._retry import ClassifyContext, classify_attempt, full_jitter
from epostix.models import EmailCreate

FAST = RetryPolicy(backoff_base=0.001, backoff_maximum=0.001)


def a_send() -> EmailCreate:
    return EmailCreate(from_=addr("a@example.com"), to="b@example.com", subject="s")


def an_email() -> dict[str, object]:
    return {"id": "eml_1", "status": "queued", "created_at": "2026-09-06T10:00:00Z"}


def error_reply(status: int, error_type: str, **headers: str) -> Reply:
    return Reply(
        status=status,
        body={"type": error_type, "message": "no", "request_id": "req_1", "status": status},
        headers=headers,
    )


def decide(
    *,
    retry_class: RetryClass = "SafeRead",
    error: BaseException | None = None,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    attempt_number: int = 1,
    remaining: float | None = None,
    has_idempotency_key: bool = False,
    body_is_replayable: bool = True,
    bytes_were_written: bool = False,
    response_started: bool = False,
) -> Any:
    return classify_attempt(
        ClassifyContext(
            policy=policy,
            retry_class=retry_class,
            attempt_number=attempt_number,
            remaining=policy.operation_deadline if remaining is None else remaining,
            error=error,
            has_idempotency_key=has_idempotency_key,
            body_is_replayable=body_is_replayable,
            bytes_were_written=bytes_were_written,
            response_started=response_started,
            random=lambda: 0.5,
        )
    )


def test_the_defaults_are_two_retries_over_one_operation_deadline() -> None:
    assert DEFAULT_RETRY_POLICY.maximum_retries == 2
    assert DEFAULT_RETRY_POLICY.operation_deadline == 30.0
    assert DISABLED_RETRY_POLICY.maximum_retries == 0


def test_backoff_is_full_jitter_over_an_exponential_ceiling() -> None:
    policy = RetryPolicy(backoff_base=0.25, backoff_multiplier=2, backoff_maximum=2.0)

    assert full_jitter(policy, 1, lambda: 1.0) == pytest.approx(0.25)
    assert full_jitter(policy, 2, lambda: 1.0) == pytest.approx(0.5)
    assert full_jitter(policy, 1, lambda: 0.0) == 0.0
    assert full_jitter(policy, 9, lambda: 1.0) == pytest.approx(2.0)


def test_the_attempt_budget_stops_iteration() -> None:
    decision = decide(attempt_number=3)

    assert decision.action == "stop"
    assert decision.reason == "AttemptBudgetExhausted"


def test_a_delay_that_would_outlast_the_deadline_stops_iteration() -> None:
    decision = decide(remaining=4.0)

    assert decision.action == "stop"
    assert decision.reason == "DeadlineExhausted"


def test_a_safe_read_retries_a_bare_transport_failure() -> None:
    assert decide(retry_class="SafeRead").action == "retry"
    assert decide(retry_class="NaturallyIdempotent").action == "retry"


def test_an_excluded_mutation_stops_once_bytes_were_written() -> None:
    assert decide(retry_class="ExcludedMutation", bytes_were_written=False).action == "retry"

    decision = decide(retry_class="ExcludedMutation", bytes_were_written=True)
    assert decision.action == "stop"
    assert decision.reason == "OperationExcluded"


def test_a_stream_stops_once_the_response_has_started() -> None:
    assert decide(retry_class="Streaming", response_started=False).action == "retry"

    decision = decide(retry_class="Streaming", response_started=True)
    assert decision.action == "stop"
    assert decision.reason == "NotRetryable"


def test_a_deduplicated_admission_needs_a_key_and_a_replayable_body() -> None:
    assert (
        decide(retry_class="DeduplicatedAdmission", has_idempotency_key=True).action == "retry"
    )

    without_key = decide(retry_class="DeduplicatedAdmission", has_idempotency_key=False)
    assert without_key.action == "stop"
    assert without_key.reason == "OperationExcluded"

    unreplayable = decide(
        retry_class="DeduplicatedAdmission",
        has_idempotency_key=True,
        body_is_replayable=False,
    )
    assert unreplayable.action == "stop"
    assert unreplayable.reason == "BodyNotReplayable"


def test_the_mutation_retry_mode_can_close_or_open_the_gate() -> None:
    never = decide(
        retry_class="DeduplicatedAdmission",
        has_idempotency_key=True,
        policy=RetryPolicy(mutation_retry="Never"),
    )
    assert never.action == "stop"
    assert never.reason == "OperationExcluded"

    always = decide(
        retry_class="DeduplicatedAdmission",
        has_idempotency_key=False,
        policy=RetryPolicy(mutation_retry="Always"),
    )
    assert always.action == "retry"


def test_a_daily_quota_never_sleeps(clients: Clients) -> None:
    recorder = Recorder(
        replies(error_reply(429, "daily_quota_exceeded", **{"Retry-After": "31200"}))
    )
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixQuotaExceededError) as raised:
        client.emails.get_email("eml_1")

    assert recorder.count == 1
    assert raised.value.response_metadata.retries_exhausted_reason == "NotRetryable"
    assert raised.value.retry_after == 31200


def test_a_monthly_quota_reports_no_next_attempt(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(429, "monthly_quota_exceeded")))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixQuotaExceededError) as raised:
        client.emails.get_email("eml_1")

    assert recorder.count == 1
    assert raised.value.next_attempt_at is None


def test_a_rate_limit_honours_retry_after(clients: Clients) -> None:
    recorder = Recorder(
        [
            error_reply(429, "rate_limit_exceeded", **{"Retry-After": "0"}),
            Reply(body=an_email()),
        ]
    )
    client = clients.sync(recorder, retry_policy=FAST)

    client.emails.get_email("eml_1")

    assert recorder.count == 2


def test_a_retry_after_beyond_the_deadline_stops(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(429, "rate_limit_exceeded", **{"Retry-After": "600"})))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixRateLimitError) as raised:
        client.emails.get_email("eml_1")

    assert recorder.count == 1
    assert raised.value.response_metadata.retries_exhausted_reason == "RetryAfterExceedsDeadline"


def test_an_idempotency_conflict_never_retries_and_never_mints_a_new_key(
    clients: Clients,
) -> None:
    recorder = Recorder(replies(error_reply(409, "idempotency_conflict")))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixIdempotencyConflictError):
        client.emails.send_email(a_send(), idempotency_key="order-4821")

    assert recorder.count == 1
    assert recorder.requests[0].headers["idempotency-key"] == "order-4821"


def test_an_idempotency_in_progress_retries_with_the_same_key(clients: Clients) -> None:
    recorder = Recorder(
        [
            error_reply(409, "idempotency_in_progress", **{"Retry-After": "0"}),
            Reply(body=an_email()),
        ]
    )
    client = clients.sync(recorder, retry_policy=FAST)

    client.emails.send_email(a_send(), idempotency_key="order-4821")

    assert recorder.count == 2
    assert {request.headers["idempotency-key"] for request in recorder.requests} == {"order-4821"}


def test_a_client_error_is_never_retried(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(422, "validation_error")))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(Exception):
        client.emails.get_email("eml_1")

    assert recorder.count == 1


def test_an_unknown_error_type_is_never_retryable_by_inference(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(422, "quantum_invalid")))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(Exception):
        client.emails.get_email("eml_1")

    assert recorder.count == 1


def test_an_unknown_error_type_on_a_server_status_still_retries(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(503, "quantum_overload")))
    client = clients.sync(recorder, retry_policy=FAST)

    with pytest.raises(EpostixServerError):
        client.emails.get_email("eml_1")

    assert recorder.count == 3


def test_a_mutation_without_a_key_is_excluded_from_retry(clients: Clients) -> None:
    recorder = Recorder(replies(error_reply(503, "service_unavailable")))
    client = clients.sync(recorder, retry_policy=FAST, automatic_idempotency_keys=False)

    with pytest.raises(EpostixServerError) as raised:
        client.emails.send_email(a_send())

    assert recorder.count == 1
    assert raised.value.response_metadata.retries_exhausted_reason == "OperationExcluded"


async def test_the_async_client_retries_the_same_way(clients: Clients) -> None:
    recorder = Recorder(
        [error_reply(503, "service_unavailable"), Reply(body=an_email())]
    )
    client = clients.asynchronous(recorder, retry_policy=FAST)

    await client.emails.send_email(a_send())

    assert recorder.count == 2
    await client.aclose()


async def test_the_async_client_reports_a_lost_response_the_same_way(clients: Clients) -> None:
    import httpx

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    from epostix import AsyncEpostix

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    client = AsyncEpostix(
        "tix_test_abcdef",
        base_url="https://api.epostix.test/v1",
        http_client=http,
        retry_policy=FAST,
    )

    with pytest.raises(EpostixOutcomeUnknownError) as raised:
        await client.emails.send_email(a_send())

    assert raised.value.replayable_with_key is True

    await http.aclose()


async def test_a_cancelled_async_call_is_never_swallowed() -> None:
    import asyncio

    import httpx

    from epostix import AsyncEpostix

    started = asyncio.Event()

    async def stall(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(60)
        return httpx.Response(200, json=an_email())

    http = httpx.AsyncClient(transport=httpx.MockTransport(stall))
    client = AsyncEpostix(
        "tix_test_abcdef",
        base_url="https://api.epostix.test/v1",
        http_client=http,
        retry_policy=FAST,
    )

    task = asyncio.create_task(client.emails.get_email("eml_1"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await http.aclose()


async def test_a_cancelled_backoff_sleep_is_never_swallowed() -> None:
    import asyncio

    import httpx

    from epostix import AsyncEpostix

    responded = asyncio.Event()

    async def fail(request: httpx.Request) -> httpx.Response:
        responded.set()
        return httpx.Response(503, json={"type": "service_unavailable", "message": "down"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    client = AsyncEpostix(
        "tix_test_abcdef",
        base_url="https://api.epostix.test/v1",
        http_client=http,
        retry_policy=RetryPolicy(
            backoff_base=5.0, backoff_maximum=5.0, operation_deadline=300.0
        ),
        random=lambda: 1.0,
    )

    task = asyncio.create_task(client.emails.get_email("eml_1"))
    await responded.wait()
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    await http.aclose()

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

import httpx

from ._codec import Codec, format_datetime
from ._errors import (
    EpostixAPIError,
    EpostixConfigurationError,
    EpostixOutcomeUnknownError,
    EpostixTimeoutError,
    EpostixTransportError,
    FieldError,
    build_api_error,
)
from ._model import Model
from ._response import (
    AttemptRecord,
    ResponseMetadata,
    RetriesExhaustedReason,
    rate_limit_from,
    request_id_from,
    retry_after_from,
)
from ._retry import (
    ClassifyContext,
    RetryClass,
    RetryPolicy,
    classify_attempt,
)
from ._types import NOT_GIVEN, NotGiven

IDEMPOTENCY_KEY_MAXIMUM_LENGTH = 255
IDEMPOTENCY_RETENTION_HOURS = 24
RAW_BODY_SNIPPET_LIMIT = 8192


@dataclass(frozen=True)
class OperationSpec:
    id: str
    method: str
    path_literals: tuple[str, ...]
    path_params: tuple[str, ...]
    success_status: int
    empty: bool
    binary: bool
    idempotent: bool
    retry_class: RetryClass


@dataclass(frozen=True)
class RequestOptions:
    retry_policy: RetryPolicy | None = None
    operation_deadline: float | None = None
    headers: Mapping[str, str] | None = None
    base_url: str | None = None


@dataclass(frozen=True)
class TransportConfig:
    api_key: str
    base_url: str
    retry_policy: RetryPolicy
    user_agent: str
    automatic_idempotency_keys: bool
    default_headers: Mapping[str, str]
    random: Callable[[], float]
    now: Callable[[], float]


@dataclass
class _Plan:
    spec: OperationSpec
    url: str
    headers: dict[str, str]
    body: bytes | None
    decode: Codec | None
    idempotency_key: str | None
    policy: RetryPolicy
    deadline: float


@dataclass
class _Attempts:
    started: float
    number: int = 0
    slept_before: float = 0.0
    bytes_were_written: bool = False
    last_error: BaseException | None = None
    records: list[AttemptRecord] = field(default_factory=list)


def _query_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def build_query(query: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if query is None:
        return []

    out: list[tuple[str, str]] = []

    for key, value in query.items():
        if value is None or isinstance(value, NotGiven):
            continue

        if isinstance(value, (list, tuple)):
            out.extend((key, _query_value(item)) for item in value)
            continue

        out.append((key, _query_value(value)))

    return out


def build_path(spec: OperationSpec, values: Sequence[str]) -> str:
    out = ""

    for index, literal in enumerate(spec.path_literals):
        out += literal

        if index < len(spec.path_params):
            value = values[index] if index < len(values) else ""
            if not value:
                raise EpostixConfigurationError(
                    f"{spec.id}: path parameter \"{spec.path_params[index]}\" is required"
                )
            out += quote(value, safe="")

    return out


def generate_idempotency_key() -> str:
    return "epx_auto_" + secrets.token_hex(16)


def _details_of(body: Any) -> list[FieldError]:
    if not isinstance(body, dict):
        return []

    raw = body.get("details")
    if not isinstance(raw, list):
        return []

    return [
        FieldError(
            field=str(entry.get("field", "")),
            message=str(entry.get("message", "")),
            code=str(entry.get("code", "")),
        )
        for entry in raw
        if isinstance(entry, dict)
    ]


class _TransportCore:
    def __init__(self, config: TransportConfig) -> None:
        self._config = config

    @property
    def config(self) -> TransportConfig:
        return self._config

    def _plan_request(
        self,
        spec: OperationSpec,
        path: Sequence[str],
        query: Mapping[str, Any] | None,
        body: Any,
        encode: Codec | None,
        decode: Codec | None,
        idempotency_key: str | None,
        options: RequestOptions | None,
    ) -> _Plan:
        policy = (options.retry_policy if options else None) or self._config.retry_policy
        deadline = (options.operation_deadline if options else None) or policy.operation_deadline

        key = self._resolve_idempotency_key(spec, idempotency_key)
        payload = self._serialize_body(body, encode)

        base = (options.base_url if options else None) or self._config.base_url
        url = base.rstrip("/") + build_path(spec, path)

        headers = self._base_headers(options, "application/json")

        if payload is not None:
            headers["content-type"] = "application/json"
        if key is not None:
            headers["idempotency-key"] = key

        pairs = build_query(query)
        if pairs:
            url = str(httpx.URL(url, params=pairs))

        return _Plan(
            spec=spec,
            url=url,
            headers=headers,
            body=payload,
            decode=decode,
            idempotency_key=key,
            policy=policy,
            deadline=deadline,
        )

    def _base_headers(self, options: RequestOptions | None, accept: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "accept": accept,
            "authorization": f"Bearer {self._config.api_key}",
            "user-agent": self._config.user_agent,
        }
        headers.update(self._config.default_headers)

        if options is not None and options.headers is not None:
            headers.update(options.headers)

        return headers

    def _plan_raw(
        self,
        spec: OperationSpec,
        path: Sequence[str],
        query: Mapping[str, Any] | None,
        options: RequestOptions | None,
    ) -> _Plan:
        policy = (options.retry_policy if options else None) or self._config.retry_policy
        deadline = (options.operation_deadline if options else None) or policy.operation_deadline

        base = (options.base_url if options else None) or self._config.base_url
        url = base.rstrip("/") + build_path(spec, path)

        pairs = build_query(query)
        if pairs:
            url = str(httpx.URL(url, params=pairs))

        return _Plan(
            spec=spec,
            url=url,
            headers=self._base_headers(options, "*/*"),
            body=None,
            decode=None,
            idempotency_key=None,
            policy=policy,
            deadline=deadline,
        )

    def _resolve_idempotency_key(self, spec: OperationSpec, supplied: str | None) -> str | None:
        if supplied is not None:
            if not spec.idempotent:
                raise EpostixConfigurationError(
                    f"{spec.id} does not support an idempotency key:"
                    " the server does not deduplicate this operation"
                )
            if len(supplied) > IDEMPOTENCY_KEY_MAXIMUM_LENGTH:
                raise EpostixConfigurationError(
                    f"idempotency key must be {IDEMPOTENCY_KEY_MAXIMUM_LENGTH} characters or fewer"
                )
            return supplied

        if not spec.idempotent or not self._config.automatic_idempotency_keys:
            return None

        return generate_idempotency_key()

    def _serialize_body(self, body: Any, encode: Codec | None) -> bytes | None:
        if body is None or isinstance(body, NotGiven):
            return None

        encoded = encode.encode(body) if encode is not None else body

        return json.dumps(encoded, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _attempt_timeout(self, policy: RetryPolicy, remaining: float) -> httpx.Timeout:
        read = min(remaining, policy.read_timeout)
        if read <= 0:
            read = 0.001

        return httpx.Timeout(
            connect=min(policy.connect_timeout, read),
            read=read,
            write=read,
            pool=policy.connect_timeout,
        )

    def _metadata_of(self, response: httpx.Response, plan: _Plan, attempts: _Attempts) -> ResponseMetadata:
        headers = response.headers

        return ResponseMetadata(
            status_code=response.status_code,
            request_id=request_id_from(headers),
            rate_limit=rate_limit_from(headers),
            retry_after=retry_after_from(headers),
            idempotent_replayed=headers.get("idempotent-replayed") == "true",
            idempotency_key=plan.idempotency_key,
            attempts=attempts.number,
            elapsed=self._config.now() - attempts.started,
            attempt_records=attempts.records,
        )

    def _decode_success(self, plan: _Plan, response: httpx.Response, metadata: ResponseMetadata) -> Any:
        if plan.spec.empty:
            return None

        text = response.text
        if len(text) == 0:
            return None

        parsed = json.loads(text)
        decoded = plan.decode.decode(parsed) if plan.decode is not None else parsed

        if isinstance(decoded, Model):
            decoded.response_metadata = metadata

        return decoded

    def _error_of(self, response: httpx.Response, metadata: ResponseMetadata) -> EpostixAPIError:
        text = response.text

        try:
            parsed: Any = json.loads(text)
        except ValueError:
            parsed = None

        if not isinstance(parsed, dict) or not isinstance(parsed.get("type"), str):
            snippet = text[:RAW_BODY_SNIPPET_LIMIT]

            return build_api_error(
                status=response.status_code,
                type="",
                api_message=response.reason_phrase or "the server returned a non-JSON error response",
                request_id=metadata.request_id,
                details=[],
                response_metadata=metadata,
                raw_body_snippet=snippet,
                raw_body_truncated=len(text) > len(snippet),
            )

        status = parsed["status"] if isinstance(parsed.get("status"), int) else response.status_code
        request_id = parsed["request_id"] if isinstance(parsed.get("request_id"), str) else metadata.request_id
        doc_url = parsed["doc_url"] if isinstance(parsed.get("doc_url"), str) else None
        message = parsed["message"] if isinstance(parsed.get("message"), str) else ""

        return build_api_error(
            status=status,
            type=parsed["type"],
            api_message=message,
            request_id=request_id,
            details=_details_of(parsed),
            response_metadata=metadata,
            doc_url=doc_url,
        )

    def _transport_error(self, cause: httpx.HTTPError, attempts: _Attempts) -> BaseException:
        metadata = ResponseMetadata(
            attempts=attempts.number,
            elapsed=self._config.now() - attempts.started,
            attempt_records=attempts.records,
        )

        if isinstance(cause, httpx.ConnectTimeout):
            return EpostixTimeoutError("connect", cause, metadata)

        if isinstance(cause, httpx.TimeoutException):
            return EpostixTimeoutError("read", cause, metadata)

        return EpostixTransportError("the request could not be completed", cause, metadata)

    def _classify(self, plan: _Plan, attempts: _Attempts) -> Any:
        return classify_attempt(
            ClassifyContext(
                policy=plan.policy,
                retry_class=plan.spec.retry_class,
                attempt_number=attempts.number,
                remaining=plan.deadline - (self._config.now() - attempts.started),
                error=attempts.last_error,
                has_idempotency_key=plan.idempotency_key is not None,
                body_is_replayable=True,
                bytes_were_written=attempts.bytes_were_written,
                response_started=False,
                random=self._config.random,
            )
        )

    def _final_error(
        self,
        plan: _Plan,
        attempts: _Attempts,
        exhausted: RetriesExhaustedReason | None,
    ) -> BaseException:
        last = attempts.last_error

        if isinstance(last, EpostixAPIError):
            last.response_metadata.retries_exhausted_reason = exhausted
            last.response_metadata.attempts = attempts.number
            last.response_metadata.attempt_records = attempts.records

            return last

        if plan.spec.method != "GET" and attempts.bytes_were_written:
            metadata = ResponseMetadata(
                idempotency_key=plan.idempotency_key,
                attempts=attempts.number,
                elapsed=self._config.now() - attempts.started,
                attempt_records=attempts.records,
                retries_exhausted_reason=exhausted,
            )

            return EpostixOutcomeUnknownError(
                plan.spec.id,
                plan.idempotency_key,
                plan.idempotency_key is not None
                and plan.spec.retry_class == "DeduplicatedAdmission",
                metadata,
                last,
            )

        return last if last is not None else EpostixTransportError(
            "the request could not be completed", None, ResponseMetadata()
        )

    def _record_success(self, attempts: _Attempts, started: float, status: int) -> None:
        attempts.records.append(
            AttemptRecord(
                attempt_number=attempts.number,
                started_at=started,
                elapsed=self._config.now() - started,
                status_code=status,
                error_type=None,
                retry_after=None,
                slept_before=attempts.slept_before,
            )
        )

    def _record_api_error(
        self, attempts: _Attempts, started: float, error: EpostixAPIError
    ) -> None:
        attempts.records.append(
            AttemptRecord(
                attempt_number=attempts.number,
                started_at=started,
                elapsed=self._config.now() - started,
                status_code=error.status,
                error_type=error.type,
                retry_after=error.response_metadata.retry_after,
                slept_before=attempts.slept_before,
            )
        )

    def _record_failure(self, attempts: _Attempts, started: float) -> None:
        attempts.records.append(
            AttemptRecord(
                attempt_number=attempts.number,
                started_at=started,
                elapsed=self._config.now() - started,
                status_code=None,
                error_type=None,
                retry_after=None,
                slept_before=attempts.slept_before,
            )
        )


def build_http_client(policy: RetryPolicy) -> httpx.Client:
    return httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        timeout=httpx.Timeout(
            connect=policy.connect_timeout,
            read=policy.read_timeout,
            write=policy.read_timeout,
            pool=policy.connect_timeout,
        ),
        follow_redirects=False,
    )


def build_async_http_client(policy: RetryPolicy) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=0),
        timeout=httpx.Timeout(
            connect=policy.connect_timeout,
            read=policy.read_timeout,
            write=policy.read_timeout,
            pool=policy.connect_timeout,
        ),
        follow_redirects=False,
    )


class Transport(_TransportCore):
    def __init__(self, config: TransportConfig, client: httpx.Client, owns_client: bool) -> None:
        super().__init__(config)
        self._client = client
        self._owns_client = owns_client

    @property
    def http_client(self) -> httpx.Client:
        return self._client

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def execute_raw(
        self,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[httpx.Response, ResponseMetadata]:
        plan = self._plan_raw(spec, path, query, options)
        attempts = _Attempts(started=self._config.now(), number=1)

        request = self._client.build_request(
            plan.spec.method,
            plan.url,
            headers=plan.headers,
            timeout=self._attempt_timeout(plan.policy, plan.deadline),
        )
        response = self._client.send(request, stream=True)
        metadata = self._metadata_of(response, plan, attempts)

        if not 200 <= response.status_code < 300:
            response.read()
            error = self._error_of(response, metadata)
            response.close()

            raise error

        return response, metadata

    def execute(
        self,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        body: Any = NOT_GIVEN,
        encode: Codec | None = None,
        decode: Codec | None = None,
        idempotency_key: str | None = None,
        options: RequestOptions | None = None,
    ) -> Any:
        plan = self._plan_request(spec, path, query, body, encode, decode, idempotency_key, options)
        attempts = _Attempts(started=self._config.now())
        exhausted: RetriesExhaustedReason | None = None

        while True:
            attempts.number += 1
            attempt_started = self._config.now()
            remaining = plan.deadline - (attempt_started - attempts.started)

            try:
                response = self._client.request(
                    plan.spec.method,
                    plan.url,
                    headers=plan.headers,
                    content=plan.body,
                    timeout=self._attempt_timeout(plan.policy, remaining),
                )
            except httpx.HTTPError as error:
                if plan.body is not None:
                    attempts.bytes_were_written = True
                attempts.last_error = self._transport_error(error, attempts)
                self._record_failure(attempts, attempt_started)
            else:
                attempts.bytes_were_written = True
                metadata = self._metadata_of(response, plan, attempts)

                if 200 <= response.status_code < 300:
                    self._record_success(attempts, attempt_started, response.status_code)
                    metadata.attempts = attempts.number
                    metadata.elapsed = self._config.now() - attempts.started
                    metadata.attempt_records = attempts.records

                    return self._decode_success(plan, response, metadata)

                api_error = self._error_of(response, metadata)
                attempts.last_error = api_error
                self._record_api_error(attempts, attempt_started, api_error)

            decision = self._classify(plan, attempts)

            if decision.action == "stop":
                exhausted = decision.reason
                break

            attempts.slept_before = decision.delay
            threading.Event().wait(decision.delay)

        raise self._final_error(plan, attempts, exhausted)


class AsyncTransport(_TransportCore):
    def __init__(
        self, config: TransportConfig, client: httpx.AsyncClient, owns_client: bool
    ) -> None:
        super().__init__(config)
        self._client = client
        self._owns_client = owns_client

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._client

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def execute_raw(
        self,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        options: RequestOptions | None = None,
    ) -> tuple[httpx.Response, ResponseMetadata]:
        plan = self._plan_raw(spec, path, query, options)
        attempts = _Attempts(started=self._config.now(), number=1)

        request = self._client.build_request(
            plan.spec.method,
            plan.url,
            headers=plan.headers,
            timeout=self._attempt_timeout(plan.policy, plan.deadline),
        )
        response = await self._client.send(request, stream=True)
        metadata = self._metadata_of(response, plan, attempts)

        if not 200 <= response.status_code < 300:
            await response.aread()
            error = self._error_of(response, metadata)
            await response.aclose()

            raise error

        return response, metadata

    async def execute(
        self,
        spec: OperationSpec,
        *,
        path: Sequence[str] = (),
        query: Mapping[str, Any] | None = None,
        body: Any = NOT_GIVEN,
        encode: Codec | None = None,
        decode: Codec | None = None,
        idempotency_key: str | None = None,
        options: RequestOptions | None = None,
    ) -> Any:
        plan = self._plan_request(spec, path, query, body, encode, decode, idempotency_key, options)
        attempts = _Attempts(started=self._config.now())
        exhausted: RetriesExhaustedReason | None = None

        while True:
            attempts.number += 1
            attempt_started = self._config.now()
            remaining = plan.deadline - (attempt_started - attempts.started)

            try:
                response = await self._client.request(
                    plan.spec.method,
                    plan.url,
                    headers=plan.headers,
                    content=plan.body,
                    timeout=self._attempt_timeout(plan.policy, remaining),
                )
            except asyncio.CancelledError:
                raise
            except httpx.HTTPError as error:
                if plan.body is not None:
                    attempts.bytes_were_written = True
                attempts.last_error = self._transport_error(error, attempts)
                self._record_failure(attempts, attempt_started)
            else:
                attempts.bytes_were_written = True
                metadata = self._metadata_of(response, plan, attempts)

                if 200 <= response.status_code < 300:
                    self._record_success(attempts, attempt_started, response.status_code)
                    metadata.attempts = attempts.number
                    metadata.elapsed = self._config.now() - attempts.started
                    metadata.attempt_records = attempts.records

                    return self._decode_success(plan, response, metadata)

                api_error = self._error_of(response, metadata)
                attempts.last_error = api_error
                self._record_api_error(attempts, attempt_started, api_error)

            decision = self._classify(plan, attempts)

            if decision.action == "stop":
                exhausted = decision.reason
                break

            attempts.slept_before = decision.delay
            await asyncio.sleep(decision.delay)

        raise self._final_error(plan, attempts, exhausted)

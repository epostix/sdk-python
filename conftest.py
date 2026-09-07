from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence, cast

import httpx
import pytest

from epostix import AsyncEpostix, Epostix
from epostix._codec import Codec
from epostix._generated import codecs
from epostix._retry import DISABLED_RETRY_POLICY, RetryPolicy

FIXTURES = Path(__file__).parent / "tests" / "fixtures"

BASE_URL = "https://api.epostix.test/v1"
API_KEY = "tix_test_abcdef"

def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def screaming(name: str) -> str:
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)

    return name.upper()


def codec_for(type_name: str) -> Codec:
    codec = getattr(codecs, "CODEC_" + screaming(type_name), None)

    if codec is None:
        raise AssertionError(f"no codec for fixture type {type_name}")

    return cast(Codec, codec)


@dataclass
class Reply:
    status: int = 200
    body: Any = None
    text: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Recorder:
    replies: list[Reply]
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index = min(len(self.requests), len(self.replies) - 1)
        self.requests.append(request)
        reply = self.replies[index]

        if reply.text is not None:
            return httpx.Response(reply.status, text=reply.text, headers=reply.headers)

        return httpx.Response(reply.status, json=reply.body, headers=reply.headers)

    @property
    def count(self) -> int:
        return len(self.requests)

    def query_of(self, index: int) -> dict[str, list[str]]:
        params = self.requests[index].url.params

        return {key: params.get_list(key) for key in params.keys()}


@dataclass
class Clients:
    _closers: list[Callable[[], None]] = field(default_factory=list)

    def sync(
        self,
        recorder: Recorder,
        *,
        retry_policy: RetryPolicy = DISABLED_RETRY_POLICY,
        automatic_idempotency_keys: bool = True,
    ) -> Epostix:
        http = httpx.Client(transport=httpx.MockTransport(recorder))
        self._closers.append(http.close)

        return Epostix(
            API_KEY,
            base_url=BASE_URL,
            http_client=http,
            retry_policy=retry_policy,
            automatic_idempotency_keys=automatic_idempotency_keys,
        )

    def asynchronous(
        self,
        recorder: Recorder,
        *,
        retry_policy: RetryPolicy = DISABLED_RETRY_POLICY,
        automatic_idempotency_keys: bool = True,
    ) -> AsyncEpostix:
        http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))

        return AsyncEpostix(
            API_KEY,
            base_url=BASE_URL,
            http_client=http,
            retry_policy=retry_policy,
            automatic_idempotency_keys=automatic_idempotency_keys,
        )

    def close(self) -> None:
        for closer in self._closers:
            closer()


@pytest.fixture
def clients() -> Iterator[Clients]:
    built = Clients()
    yield built
    built.close()


def replies(*items: Reply) -> list[Reply]:
    return list(items)


def pages(bodies: Sequence[Any]) -> list[Reply]:
    return [Reply(status=200, body=body) for body in bodies]

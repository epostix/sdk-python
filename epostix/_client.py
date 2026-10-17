from __future__ import annotations

import random as _random
import time
from types import TracebackType
from typing import Callable, Literal, Mapping, TypeAlias
from urllib.parse import urlparse

import httpx

from ._errors import EpostixConfigurationError
from ._generated.resources import build_async_resources, build_resources
from ._generated.resources.analytics import AnalyticsResource, AsyncAnalyticsResource
from ._generated.resources.api_keys import ApiKeysResource, AsyncApiKeysResource
from ._generated.resources.broadcasts import AsyncBroadcastsResource, BroadcastsResource
from ._generated.resources.contacts import AsyncContactsResource, ContactsResource
from ._generated.resources.domains import AsyncDomainsResource, DomainsResource
from ._generated.resources.emails import AsyncEmailsResource, EmailsResource
from ._generated.resources.events import AsyncEventsResource, EventsResource
from ._generated.resources.suppressions import AsyncSuppressionsResource, SuppressionsResource
from ._generated.resources.tags import AsyncTagsResource, TagsResource
from ._generated.resources.templates import AsyncTemplatesResource, TemplatesResource
from ._generated.resources.webhooks import AsyncWebhooksResource, WebhooksResource
from ._retry import DEFAULT_RETRY_POLICY, RetryPolicy
from ._transport import (
    AsyncTransport,
    TransportConfig,
    Transport,
    build_async_http_client,
    build_http_client,
)
from .overrides import (
    AsyncAttachmentsResource,
    AsyncInboundResource,
    AttachmentsResource,
    InboundResource,
)

PRODUCTION_BASE_URL = "https://api.epostix.com/v1"
STAGING_BASE_URL = "https://api.staging.epostix.com/v1"
SANDBOX_DOMAIN = "epostix.dev"
DEDUPLICATION_SCOPE = "api_key + environment + method + path + key, 24h"
USER_AGENT = "epostix-python/1.1.0"

KeyEnvironment: TypeAlias = Literal["live", "test", "unrecognized"]


def key_environment_of(api_key: str) -> KeyEnvironment:
    if api_key.startswith("tix_live_"):
        return "live"

    if api_key.startswith("tix_test_"):
        return "test"

    return "unrecognized"


def _build_config(
    api_key: str,
    base_url: str,
    retry_policy: RetryPolicy,
    default_headers: Mapping[str, str] | None,
    user_agent_suffix: str | None,
    automatic_idempotency_keys: bool,
    random: Callable[[], float] | None,
    now: Callable[[], float] | None,
) -> TransportConfig:
    if not api_key or not api_key.strip():
        raise EpostixConfigurationError("an API key is required")

    if api_key.startswith("Bearer "):
        raise EpostixConfigurationError(
            "pass the API key alone; the client adds the Bearer prefix itself"
        )

    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise EpostixConfigurationError(f"base_url is not a valid URL: {base_url}")

    return TransportConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        retry_policy=retry_policy,
        user_agent=USER_AGENT + (f" {user_agent_suffix}" if user_agent_suffix else ""),
        automatic_idempotency_keys=automatic_idempotency_keys,
        default_headers=dict(default_headers or {}),
        random=random or _random.random,
        now=now or time.monotonic,
    )


class Epostix:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = PRODUCTION_BASE_URL,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        http_client: httpx.Client | None = None,
        default_headers: Mapping[str, str] | None = None,
        user_agent_suffix: str | None = None,
        automatic_idempotency_keys: bool = True,
        random: Callable[[], float] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        config = _build_config(
            api_key,
            base_url,
            retry_policy,
            default_headers,
            user_agent_suffix,
            automatic_idempotency_keys,
            random,
            now,
        )

        owns_client = http_client is None
        client = http_client if http_client is not None else build_http_client(retry_policy)

        self._transport = Transport(config, client, owns_client)
        resources = build_resources(self._transport)

        self.analytics: AnalyticsResource = resources.analytics
        self.api_keys: ApiKeysResource = resources.api_keys
        self.attachments = AttachmentsResource(self._transport)
        self.broadcasts: BroadcastsResource = resources.broadcasts
        self.contacts: ContactsResource = resources.contacts
        self.domains: DomainsResource = resources.domains
        self.emails: EmailsResource = resources.emails
        self.events: EventsResource = resources.events
        self.inbound = InboundResource(self._transport)
        self.suppressions: SuppressionsResource = resources.suppressions
        self.tags: TagsResource = resources.tags
        self.templates: TemplatesResource = resources.templates
        self.webhooks: WebhooksResource = resources.webhooks

    @property
    def key_environment(self) -> KeyEnvironment:
        return key_environment_of(self._transport.config.api_key)

    @property
    def deduplication_scope(self) -> str:
        return DEDUPLICATION_SCOPE

    def with_api_key(self, api_key: str) -> Epostix:
        config = self._transport.config

        return Epostix(
            api_key,
            base_url=config.base_url,
            retry_policy=config.retry_policy,
            http_client=self._transport.http_client,
            default_headers=config.default_headers,
            automatic_idempotency_keys=config.automatic_idempotency_keys,
            random=config.random,
            now=config.now,
        )

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> Epostix:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncEpostix:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = PRODUCTION_BASE_URL,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        http_client: httpx.AsyncClient | None = None,
        default_headers: Mapping[str, str] | None = None,
        user_agent_suffix: str | None = None,
        automatic_idempotency_keys: bool = True,
        random: Callable[[], float] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        config = _build_config(
            api_key,
            base_url,
            retry_policy,
            default_headers,
            user_agent_suffix,
            automatic_idempotency_keys,
            random,
            now,
        )

        owns_client = http_client is None
        client = http_client if http_client is not None else build_async_http_client(retry_policy)

        self._transport = AsyncTransport(config, client, owns_client)
        resources = build_async_resources(self._transport)

        self.analytics: AsyncAnalyticsResource = resources.analytics
        self.api_keys: AsyncApiKeysResource = resources.api_keys
        self.attachments = AsyncAttachmentsResource(self._transport)
        self.broadcasts: AsyncBroadcastsResource = resources.broadcasts
        self.contacts: AsyncContactsResource = resources.contacts
        self.domains: AsyncDomainsResource = resources.domains
        self.emails: AsyncEmailsResource = resources.emails
        self.events: AsyncEventsResource = resources.events
        self.inbound = AsyncInboundResource(self._transport)
        self.suppressions: AsyncSuppressionsResource = resources.suppressions
        self.tags: AsyncTagsResource = resources.tags
        self.templates: AsyncTemplatesResource = resources.templates
        self.webhooks: AsyncWebhooksResource = resources.webhooks

    @property
    def key_environment(self) -> KeyEnvironment:
        return key_environment_of(self._transport.config.api_key)

    @property
    def deduplication_scope(self) -> str:
        return DEDUPLICATION_SCOPE

    def with_api_key(self, api_key: str) -> AsyncEpostix:
        config = self._transport.config

        return AsyncEpostix(
            api_key,
            base_url=config.base_url,
            retry_policy=config.retry_policy,
            http_client=self._transport.http_client,
            default_headers=config.default_headers,
            automatic_idempotency_keys=config.automatic_idempotency_keys,
            random=config.random,
            now=config.now,
        )

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> AsyncEpostix:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

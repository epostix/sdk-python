from __future__ import annotations

import base64
from types import TracebackType
from typing import AsyncIterator, Iterator, cast

import httpx

from ._errors import EpostixConfigurationError
from ._generated import codecs
from ._generated.models import AttachmentResponse, AttachmentUpload
from ._generated.operations import OPERATIONS
from ._generated.resources.attachments import (
    AsyncAttachmentsResource as GeneratedAsyncAttachmentsResource,
)
from ._generated.resources.attachments import (
    AttachmentsResource as GeneratedAttachmentsResource,
)
from ._generated.resources.inbound import (
    AsyncInboundResource as GeneratedAsyncInboundResource,
)
from ._generated.resources.inbound import (
    InboundResource as GeneratedInboundResource,
)
from ._response import ResponseMetadata
from ._transport import RequestOptions
from .attachments import assert_within_limits

DEFAULT_RAW_CONTENT_TYPE = "message/rfc822"


def _upload_body(filename: str, content: bytes, content_type: str) -> AttachmentUpload:
    assert_within_limits([(filename, len(content))])

    return AttachmentUpload(
        filename=filename,
        content=base64.b64encode(content).decode("ascii"),
        content_type=content_type,
    )


def _check_cap(maximum_bytes: int) -> None:
    if maximum_bytes <= 0:
        raise EpostixConfigurationError(
            "get_inbound_email_raw_bytes requires a positive maximum_bytes"
        )


def _too_large(maximum_bytes: int) -> EpostixConfigurationError:
    return EpostixConfigurationError(
        f"the raw message is larger than the {maximum_bytes} byte cap you set"
    )


class RawContent:
    def __init__(self, response: httpx.Response, response_metadata: ResponseMetadata) -> None:
        self._response = response
        self.content_type = response.headers.get("content-type") or DEFAULT_RAW_CONTENT_TYPE
        self.response_metadata = response_metadata

    def iter_bytes(self, chunk_size: int | None = None) -> Iterator[bytes]:
        return self._response.iter_bytes(chunk_size)

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> RawContent:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class AsyncRawContent:
    def __init__(self, response: httpx.Response, response_metadata: ResponseMetadata) -> None:
        self._response = response
        self.content_type = response.headers.get("content-type") or DEFAULT_RAW_CONTENT_TYPE
        self.response_metadata = response_metadata

    def aiter_bytes(self, chunk_size: int | None = None) -> AsyncIterator[bytes]:
        return self._response.aiter_bytes(chunk_size)

    async def aclose(self) -> None:
        await self._response.aclose()

    async def __aenter__(self) -> AsyncRawContent:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


class AttachmentsResource(GeneratedAttachmentsResource):
    def upload_attachment(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        options: RequestOptions | None = None,
    ) -> AttachmentResponse:
        body = _upload_body(filename, content, content_type)
        return cast(
            AttachmentResponse,
            self._transport.execute(
                OPERATIONS["uploadAttachment"],
                body=body,
                encode=codecs.CODEC_ATTACHMENT_UPLOAD,
                decode=codecs.CODEC_ATTACHMENT_RESPONSE,
                options=options,
            ),
        )


class AsyncAttachmentsResource(GeneratedAsyncAttachmentsResource):
    async def upload_attachment(
        self,
        filename: str,
        content: bytes,
        content_type: str,
        *,
        options: RequestOptions | None = None,
    ) -> AttachmentResponse:
        body = _upload_body(filename, content, content_type)
        return cast(
            AttachmentResponse,
            await self._transport.execute(
                OPERATIONS["uploadAttachment"],
                body=body,
                encode=codecs.CODEC_ATTACHMENT_UPLOAD,
                decode=codecs.CODEC_ATTACHMENT_RESPONSE,
                options=options,
            ),
        )


class InboundResource(GeneratedInboundResource):
    def get_inbound_email_raw(
        self, inbound_id: str, *, options: RequestOptions | None = None
    ) -> RawContent:
        response, metadata = self._transport.execute_raw(
            OPERATIONS["getInboundEmailRaw"],
            path=(inbound_id,),
            options=options,
        )

        return RawContent(response, metadata)

    def get_inbound_email_raw_bytes(
        self,
        inbound_id: str,
        maximum_bytes: int,
        *,
        options: RequestOptions | None = None,
    ) -> bytes:
        _check_cap(maximum_bytes)

        chunks: list[bytes] = []
        total = 0

        with self.get_inbound_email_raw(inbound_id, options=options) as raw:
            for chunk in raw.iter_bytes():
                total += len(chunk)

                if total > maximum_bytes:
                    raise _too_large(maximum_bytes)

                chunks.append(chunk)

        return b"".join(chunks)


class AsyncInboundResource(GeneratedAsyncInboundResource):
    async def get_inbound_email_raw(
        self, inbound_id: str, *, options: RequestOptions | None = None
    ) -> AsyncRawContent:
        response, metadata = await self._transport.execute_raw(
            OPERATIONS["getInboundEmailRaw"],
            path=(inbound_id,),
            options=options,
        )

        return AsyncRawContent(response, metadata)

    async def get_inbound_email_raw_bytes(
        self,
        inbound_id: str,
        maximum_bytes: int,
        *,
        options: RequestOptions | None = None,
    ) -> bytes:
        _check_cap(maximum_bytes)

        chunks: list[bytes] = []
        total = 0

        async with await self.get_inbound_email_raw(inbound_id, options=options) as raw:
            async for chunk in raw.aiter_bytes():
                total += len(chunk)

                if total > maximum_bytes:
                    raise _too_large(maximum_bytes)

                chunks.append(chunk)

        return b"".join(chunks)

from __future__ import annotations

import base64
import os
from typing import IO, Iterable, Literal, TypeAlias

from ._errors import EpostixConfigurationError
from ._generated.models import (
    Attachment,
    AttachmentContentDisposition,
    AttachmentRef,
    AttachmentURL,
)

ATTACHMENT_DECODED_LIMIT_BYTES = 41943040
REQUEST_BODY_LIMIT_BYTES = 52428800

AttachmentDisposition: TypeAlias = Literal["attachment", "inline"]


class EpostixAttachmentTooLargeError(EpostixConfigurationError):
    def __init__(self, filename: str, actual_bytes: int, limit_bytes: int) -> None:
        super().__init__(
            f'attachment "{filename}" is {actual_bytes} bytes,'
            f" above the {limit_bytes} byte decoded limit"
        )
        self.filename = filename
        self.actual_bytes = actual_bytes
        self.limit_bytes = limit_bytes


class EpostixRequestTooLargeError(EpostixConfigurationError):
    def __init__(
        self, projected_encoded_bytes: int, limit_bytes: int, attachment_count: int
    ) -> None:
        super().__init__(
            f"{attachment_count} attachment(s) encode to about {projected_encoded_bytes} bytes,"
            f" above the {limit_bytes} byte request limit."
            " Upload them first, or pass a content URL."
        )
        self.projected_encoded_bytes = projected_encoded_bytes
        self.limit_bytes = limit_bytes
        self.attachment_count = attachment_count


def project_encoded_size(decoded_bytes: int) -> int:
    return ((decoded_bytes + 2) // 3) * 4


def assert_within_limits(sizes: Iterable[tuple[str, int]]) -> None:
    entries = list(sizes)
    total = 0

    for filename, size in entries:
        if size > ATTACHMENT_DECODED_LIMIT_BYTES:
            raise EpostixAttachmentTooLargeError(filename, size, ATTACHMENT_DECODED_LIMIT_BYTES)

        total += size

    projected = project_encoded_size(total)
    if projected > REQUEST_BODY_LIMIT_BYTES:
        raise EpostixRequestTooLargeError(projected, REQUEST_BODY_LIMIT_BYTES, len(entries))


def from_bytes(
    filename: str,
    content: bytes,
    content_type: str,
    *,
    disposition: AttachmentDisposition | None = None,
    content_id: str | None = None,
) -> Attachment:
    assert_within_limits([(filename, len(content))])

    attachment = Attachment(
        filename=filename,
        content=base64.b64encode(content).decode("ascii"),
        content_type=content_type,
    )

    if disposition is not None:
        attachment.content_disposition = AttachmentContentDisposition(disposition)

    if content_id is not None:
        attachment.content_id = content_id

    return attachment


def from_path(
    path: str | os.PathLike[str],
    *,
    filename: str | None = None,
    content_type: str = "application/octet-stream",
    disposition: AttachmentDisposition | None = None,
    content_id: str | None = None,
) -> Attachment:
    with open(path, "rb") as handle:
        content = handle.read()

    return from_bytes(
        filename or os.path.basename(os.fspath(path)),
        content,
        content_type,
        disposition=disposition,
        content_id=content_id,
    )


def from_file_object(
    filename: str,
    file: IO[bytes],
    content_type: str = "application/octet-stream",
    *,
    disposition: AttachmentDisposition | None = None,
    content_id: str | None = None,
) -> Attachment:
    if not file.seekable():
        raise EpostixConfigurationError(
            "from_file_object requires a seekable file so the attachment can be sized"
            " before it is encoded; read it into bytes and use from_bytes instead"
        )

    position = file.tell()
    size = file.seek(0, os.SEEK_END) - position
    file.seek(position)

    if size > ATTACHMENT_DECODED_LIMIT_BYTES:
        raise EpostixAttachmentTooLargeError(filename, size, ATTACHMENT_DECODED_LIMIT_BYTES)

    return from_bytes(
        filename,
        file.read(),
        content_type,
        disposition=disposition,
        content_id=content_id,
    )


def from_uploaded_id(attachment_id: str) -> AttachmentRef:
    return AttachmentRef(attachment_id=attachment_id)


def from_remote_url(
    filename: str, content_url: str, *, content_type: str | None = None
) -> AttachmentURL:
    attachment = AttachmentURL(filename=filename, content_url=content_url)

    if content_type is not None:
        attachment.content_type = content_type

    return attachment

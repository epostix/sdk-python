from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest

from epostix import NOT_GIVEN, EpostixConfigurationError
from epostix.attachments import (
    ATTACHMENT_DECODED_LIMIT_BYTES,
    REQUEST_BODY_LIMIT_BYTES,
    EpostixAttachmentTooLargeError,
    EpostixRequestTooLargeError,
    assert_within_limits,
    from_bytes,
    from_file_object,
    from_path,
    from_remote_url,
    from_uploaded_id,
    project_encoded_size,
)
from epostix.models import Attachment, AttachmentContentDisposition, AttachmentRef, AttachmentURL


def test_the_documented_limits_are_carried() -> None:
    assert ATTACHMENT_DECODED_LIMIT_BYTES == 41943040
    assert REQUEST_BODY_LIMIT_BYTES == 52428800


def test_the_encoded_size_is_projected_from_the_decoded_size() -> None:
    assert project_encoded_size(0) == 0
    assert project_encoded_size(1) == 4
    assert project_encoded_size(3) == 4
    assert project_encoded_size(4) == 8


def test_bytes_become_a_base64_inline_attachment() -> None:
    attachment = from_bytes("r.pdf", b"\x00\x01\x02", "application/pdf")

    assert isinstance(attachment, Attachment)
    assert attachment.content == base64.b64encode(b"\x00\x01\x02").decode("ascii")
    assert attachment.content_type == "application/pdf"
    assert attachment.content_disposition is NOT_GIVEN
    assert attachment.content_id is NOT_GIVEN


def test_an_inline_disposition_and_content_id_are_carried() -> None:
    attachment = from_bytes(
        "logo.png", b"\x00", "image/png", disposition="inline", content_id="logo"
    )

    assert attachment.content_disposition is AttachmentContentDisposition.INLINE
    assert attachment.content_id == "logo"


def test_an_oversized_attachment_is_refused_before_it_is_encoded() -> None:
    with pytest.raises(EpostixAttachmentTooLargeError) as raised:
        assert_within_limits([("huge.bin", ATTACHMENT_DECODED_LIMIT_BYTES + 1)])

    assert raised.value.limit_bytes == ATTACHMENT_DECODED_LIMIT_BYTES
    assert raised.value.filename == "huge.bin"


def test_a_set_of_attachments_that_would_overflow_the_request_is_refused() -> None:
    each = ATTACHMENT_DECODED_LIMIT_BYTES

    with pytest.raises(EpostixRequestTooLargeError) as raised:
        assert_within_limits([("a.bin", each), ("b.bin", each)])

    assert raised.value.limit_bytes == REQUEST_BODY_LIMIT_BYTES
    assert raised.value.attachment_count == 2


def test_a_file_on_disk_becomes_an_attachment(tmp_path: Path) -> None:
    path = tmp_path / "receipt.pdf"
    path.write_bytes(b"hello")

    attachment = from_path(path, content_type="application/pdf")

    assert attachment.filename == "receipt.pdf"
    assert base64.b64decode(attachment.content) == b"hello"


def test_a_seekable_file_object_becomes_an_attachment() -> None:
    attachment = from_file_object("r.pdf", io.BytesIO(b"hello"), "application/pdf")

    assert base64.b64decode(attachment.content) == b"hello"


def test_a_file_object_that_cannot_be_sized_is_refused() -> None:
    class Unseekable(io.RawIOBase):
        def seekable(self) -> bool:
            return False

    with pytest.raises(EpostixConfigurationError):
        from_file_object("r.pdf", Unseekable())  # type: ignore[arg-type]


def test_an_uploaded_reference_carries_only_its_id() -> None:
    reference = from_uploaded_id("att_9")

    assert isinstance(reference, AttachmentRef)
    assert reference.attachment_id == "att_9"


def test_a_remote_url_is_named_for_the_server_fetch() -> None:
    remote = from_remote_url("r.pdf", "https://example.com/r.pdf")

    assert isinstance(remote, AttachmentURL)
    assert remote.content_url == "https://example.com/r.pdf"
    assert remote.content_type is NOT_GIVEN


def test_there_is_no_from_url_helper() -> None:
    import epostix.attachments as module

    assert not hasattr(module, "from_url")

from __future__ import annotations

import importlib
import inspect
import pkgutil

import epostix
from epostix import EpostixError


def error_classes() -> dict[str, type[EpostixError]]:
    found: dict[str, type[EpostixError]] = {}

    for module in pkgutil.walk_packages(epostix.__path__, f"{epostix.__name__}."):
        imported = importlib.import_module(module.name)

        for name, value in vars(imported).items():
            if not inspect.isclass(value):
                continue
            if not issubclass(value, EpostixError):
                continue
            if not value.__module__.startswith(epostix.__name__):
                continue

            found[name] = value

    return found


def test_every_error_class_is_importable_from_the_package_root() -> None:
    missing = [
        name for name in error_classes() if getattr(epostix, name, None) is None
    ]

    assert missing == []


def test_a_root_export_is_the_same_object_as_its_module_export() -> None:
    for name, value in error_classes().items():
        assert getattr(epostix, name) is value


def test_the_attachment_limits_do_not_need_a_second_import_path() -> None:
    assert epostix.ATTACHMENT_DECODED_LIMIT_BYTES == 41943040
    assert epostix.REQUEST_BODY_LIMIT_BYTES == 52428800
    assert epostix.EpostixAttachmentTooLargeError is not None
    assert epostix.EpostixRequestTooLargeError is not None


def test_models_stay_behind_their_own_module() -> None:
    assert hasattr(epostix.models, "EmailCreate")
    assert not hasattr(epostix, "EmailCreate")

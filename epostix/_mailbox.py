from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mailbox:
    email: str
    name: str | None = None

    def __str__(self) -> str:
        if not self.name:
            return self.email

        return f"{self.name} <{self.email}>"


def addr(email: str) -> Mailbox:
    return Mailbox(email=email)


def named(name: str, email: str) -> Mailbox:
    return Mailbox(email=email, name=name)

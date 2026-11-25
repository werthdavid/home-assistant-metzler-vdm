"""Response building blocks and a fake session (placeholders only)."""

from __future__ import annotations

import json
from typing import Any

HOST = "192.0.2.10"


def info(
    employee_no: Any = "1001",
    *,
    time: str = "2026-11-25 08:57:25",
    minor: int = 1,
    door_no: Any = 1,
    omit_employee_no: bool = False,
) -> dict[str, Any]:
    """Build one InfoList entry including the PII fields the client must ignore."""
    entry: dict[str, Any] = {
        "major": 5,
        "minor": minor,
        "time": time,
        "cardNo": "PLACEHOLDER_CARD",
        "name": "PLACEHOLDER_NAME",
        "doorNo": door_no,
        "mask": "unknown",
    }
    if not omit_employee_no:
        entry["employeeNoString"] = employee_no
    return entry


def page(infos: list[dict[str, Any]], status: str = "OK", total: int | None = None) -> str:
    """Build an AcsEvent response body."""
    return json.dumps(
        {
            "AcsEvent": {
                "searchID": "x",
                "totalMatches": len(infos) if total is None else total,
                "responseStatusStrg": status,
                "numOfMatches": len(infos),
                "InfoList": infos,
            }
        }
    )


NO_MATCH = json.dumps(
    {"AcsEvent": {"responseStatusStrg": "NO MATCH", "numOfMatches": 0, "totalMatches": 0}}
)


class Call:
    """A recorded request."""

    def __init__(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        self.method = method
        self.url = url
        self.kwargs = kwargs


class FakeResponse:
    """Minimal aiohttp response stand-in."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _Ctx:
    def __init__(self, item: Any) -> None:
        self._item = item

    async def __aenter__(self) -> FakeResponse:
        if isinstance(self._item, BaseException):
            raise self._item
        status, body = self._item
        return FakeResponse(status, body)

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeSession:
    """Serve queued (status, body) tuples or exceptions in order."""

    def __init__(self) -> None:
        self.queue: list[Any] = []
        self.calls: list[Call] = []

    def add(self, body: str, status: int = 200) -> None:
        self.queue.append((status, body))

    def add_error(self, exc: BaseException) -> None:
        self.queue.append(exc)

    def request(self, method: str, url: str, **kwargs: Any) -> _Ctx:
        self.calls.append(Call(method, url, kwargs))
        return _Ctx(self.queue.pop(0))

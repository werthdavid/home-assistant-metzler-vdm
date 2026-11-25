"""Deduplication and allowlist logic for unlock records (no Home Assistant imports)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import logging

from .const import SEEN_TTL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AcsRecord:
    """A valid-card access record as reported by the door station."""

    time: str  # unchanged from the response, e.g. "2026-11-25 08:57:25"
    employee_no: str  # "" if the station reports no person ID
    door_no: int


@dataclass(frozen=True, slots=True)
class Unlock:
    """A new unlock by an allowed ID (empty ID: card without a person)."""

    employee_no: str
    alias: str
    door_no: int
    time: str


class AllowlistError(ValueError):
    """Raised when the allowlist text is invalid."""


def parse_allowlist(text: str) -> dict[str, str]:
    """Parse lines of "ID=Alias" (a bare "ID" uses the ID as alias)."""
    result: dict[str, str] = {}
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        employee_no, sep, alias = (part.strip() for part in line.partition("="))
        if not employee_no:
            raise AllowlistError(f"Line {line_no}: missing ID")
        if employee_no in result:
            raise AllowlistError(f"Line {line_no}: duplicate ID {employee_no}")
        result[employee_no] = alias if sep and alias else employee_no
    return result


def format_allowlist(allowlist: Mapping[str, str]) -> str:
    """Format an allowlist as lines of "ID=Alias"."""
    return "\n".join(f"{employee_no}={alias}" for employee_no, alias in allowlist.items())


class UnlockTracker:
    """Turn polled access records into new unlock events."""

    def __init__(
        self,
        allowlist: Mapping[str, str],
        seen_ttl: float = SEEN_TTL,
        *,
        allow_all: bool = False,
    ) -> None:
        """Set up the tracker.

        With allow_all every valid-card record counts, including cards without
        a person ID; the allowlist then only supplies aliases.
        """
        self._allowlist = dict(allowlist)
        self._allow_all = allow_all
        self._seen_ttl = seen_ttl
        self._seen: dict[tuple[str, str, int], float] = {}
        self._primed = False

    @property
    def primed(self) -> bool:
        """Return True once the first poll has been processed."""
        return self._primed

    def process(self, records: Iterable[AcsRecord], now: float) -> list[Unlock]:
        """Return unlocks not seen before; the first call only primes the state."""
        fresh: list[Unlock] = []
        for record in records:
            key = (record.time, record.employee_no, record.door_no)
            is_new = key not in self._seen
            self._seen[key] = now
            if not is_new or not self._primed:
                continue
            alias = self._allowlist.get(record.employee_no)
            if alias is None and self._allow_all:
                alias = record.employee_no
            if alias is None:
                _LOGGER.debug(
                    "Ignoring unlock of unknown ID %s at door %s",
                    record.employee_no,
                    record.door_no,
                )
                continue
            fresh.append(
                Unlock(record.employee_no, alias, record.door_no, record.time)
            )

        self._primed = True
        self._seen = {
            key: last_seen
            for key, last_seen in self._seen.items()
            if now - last_seen <= self._seen_ttl
        }
        return fresh

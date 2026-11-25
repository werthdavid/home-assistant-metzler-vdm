"""Pinned HTTPS ISAPI client for the door station.

Security notes: every request is HTTPS with a pinned SHA-256 certificate
fingerprint. Fields such as cardNo and name are never read. Exceptions carry
only fixed text and optionally an HTTP status, never response data.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from datetime import datetime, tzinfo
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import MAJOR, MAX_PAGES, MINOR_VALID_CARD, PAGE_SIZE, REQUEST_TIMEOUT
from .tracker import AcsRecord

_ACS_EVENT_PATH = "/ISAPI/AccessControl/AcsEvent?format=json"
_TIME_PATH = "/ISAPI/System/time"
_FINGERPRINT_LENGTH = 32


class MetzlerError(Exception):
    """Base error of the client."""


class MetzlerConnectionError(MetzlerError):
    """Timeout, connection failure or HTTP 5xx."""


class MetzlerAuthError(MetzlerError):
    """HTTP 401/403."""


class MetzlerCertificateError(MetzlerError):
    """Certificate does not match the pinned fingerprint."""


class MetzlerResponseError(MetzlerError):
    """Unexpected response format or too many pages."""


class MetzlerNotSupportedError(MetzlerError):
    """HTTP 404 / endpoint not supported."""


def parse_fingerprint(text: str) -> bytes:
    """Parse a SHA-256 fingerprint in hex (":" and whitespace tolerated)."""
    cleaned = "".join(ch for ch in text if ch != ":" and not ch.isspace())
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        raise ValueError("Invalid fingerprint") from None
    if len(raw) != _FINGERPRINT_LENGTH:
        raise ValueError("Fingerprint must be a SHA-256 value (32 bytes)")
    return raw


def format_fingerprint(raw: bytes) -> str:
    """Format raw bytes as upper-case colon-separated hex."""
    return ":".join(f"{b:02X}" for b in raw)


def create_digest_session(
    hass: HomeAssistant, username: str, password: str
) -> aiohttp.ClientSession:
    """Create a client session that authenticates with HTTP digest."""
    return async_create_clientsession(
        hass,
        middlewares=(aiohttp.DigestAuthMiddleware(username, password),),
    )


async def async_fetch_fingerprint(host: str, port: int = 443) -> bytes:
    """Return the SHA-256 of the DER certificate presented by the host.

    The certificate is not validated; no data is sent. The caller must have
    the user confirm the fingerprint before pinning it (trust on first use).
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    writer = None
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            _, writer = await asyncio.open_connection(host, port, ssl=context)
            ssl_object = writer.get_extra_info("ssl_object")
            der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            writer.close()
            await writer.wait_closed()
            writer = None
    except (OSError, TimeoutError, ssl.SSLError):
        raise MetzlerConnectionError("Could not read certificate") from None
    finally:
        if writer is not None:
            writer.close()
    if not der:
        raise MetzlerResponseError("No certificate presented")
    return hashlib.sha256(der).digest()


class MetzlerClient:
    """Client for the AcsEvent search and device time."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, fingerprint: bytes
    ) -> None:
        """Initialize; the session carries the digest authentication."""
        self._session = session
        self._base = f"https://{host}"
        self._ssl = aiohttp.Fingerprint(fingerprint)

    async def _request(self, method: str, path: str, **kwargs: Any) -> str:
        """Send one pinned request and return the body text."""
        try:
            async with self._session.request(
                method,
                f"{self._base}{path}",
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                **kwargs,
            ) as response:
                status = response.status
                if status in (401, 403):
                    raise MetzlerAuthError(f"Authentication failed (HTTP {status})")
                if status == 404:
                    raise MetzlerNotSupportedError("Not supported (HTTP 404)")
                if status >= 500:
                    raise MetzlerConnectionError(f"Device error (HTTP {status})")
                if status != 200:
                    raise MetzlerResponseError(f"Unexpected status (HTTP {status})")
                try:
                    return await response.text()
                except (UnicodeDecodeError, LookupError):
                    raise MetzlerResponseError("Unexpected response format") from None
        except aiohttp.ServerFingerprintMismatch:
            raise MetzlerCertificateError("Certificate fingerprint mismatch") from None
        except TimeoutError:
            raise MetzlerConnectionError("Request timed out") from None
        except aiohttp.ClientError:
            raise MetzlerConnectionError("Connection failed") from None

    async def async_search_valid_card_events(
        self, start: datetime, end: datetime
    ) -> list[AcsRecord]:
        """Return valid-card events in [start, end], following paging."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        search_id = uuid4().hex
        position = 0
        records: list[AcsRecord] = []
        for _ in range(MAX_PAGES):
            body = {
                "AcsEventCond": {
                    "searchID": search_id,
                    "searchResultPosition": position,
                    "maxResults": PAGE_SIZE,
                    "major": MAJOR,
                    "minor": MINOR_VALID_CARD,
                    "startTime": start.isoformat(timespec="seconds"),
                    "endTime": end.isoformat(timespec="seconds"),
                }
            }
            text = await self._request("POST", _ACS_EVENT_PATH, json=body)
            event = _parse_acs_event(text)
            infos = event.get("InfoList") or []
            if not isinstance(infos, list):
                raise MetzlerResponseError("Unexpected response format")
            records.extend(_to_records(infos))
            if event.get("responseStatusStrg") != "MORE":
                return records
            matches = event.get("numOfMatches")
            if not isinstance(matches, int) or isinstance(matches, bool) or matches <= 0:
                raise MetzlerResponseError("Paging made no progress")
            position += matches
        raise MetzlerResponseError("Too many result pages")

    async def async_get_device_time(self, default_tz: tzinfo) -> datetime:
        """Return the device's local time as an aware datetime."""
        text = await self._request("GET", _TIME_PATH)
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError:
            raise MetzlerResponseError("Unexpected response format") from None
        value = None
        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == "localTime":
                value = (element.text or "").strip()
                break
        if not value:
            raise MetzlerResponseError("Unexpected response format")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise MetzlerResponseError("Unexpected response format") from None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=default_tz)


def _parse_acs_event(text: str) -> dict[str, Any]:
    """Return the AcsEvent object of a response body."""
    try:
        data = json.loads(text)
    except ValueError:
        raise MetzlerResponseError("Unexpected response format") from None
    event = data.get("AcsEvent") if isinstance(data, dict) else None
    if not isinstance(event, dict):
        raise MetzlerResponseError("Unexpected response format")
    return event


def _to_records(infos: list[Any]) -> list[AcsRecord]:
    """Convert InfoList entries; only the needed fields are read."""
    records: list[AcsRecord] = []
    for item in infos:
        if not isinstance(item, dict):
            continue
        if item.get("major") != MAJOR or item.get("minor") != MINOR_VALID_CARD:
            continue
        time = item.get("time")
        employee_no = item.get("employeeNoString")
        door_no = item.get("doorNo")
        if not isinstance(time, str) or not time:
            continue
        if not isinstance(employee_no, str) or not employee_no.strip():
            continue
        if not isinstance(door_no, int) or isinstance(door_no, bool):
            continue
        records.append(AcsRecord(time, employee_no, door_no))
    return records

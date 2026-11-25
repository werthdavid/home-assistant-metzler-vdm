"""Tests for the pinned ISAPI client."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from custom_components.metzler_vdm.api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerClient,
    MetzlerConnectionError,
    MetzlerNotSupportedError,
    MetzlerResponseError,
    async_fetch_fingerprint,
    format_fingerprint,
    parse_fingerprint,
)
from custom_components.metzler_vdm.tracker import AcsRecord

from .fixtures import HOST, NO_MATCH, info, page

CET = timezone(timedelta(hours=1))
START = datetime(2026, 11, 25, 8, 56, tzinfo=CET)
END = datetime(2026, 11, 25, 23, 59, 59, tzinfo=CET)
REC = AcsRecord("2026-11-25 08:57:25", "1001", 1)


@pytest.fixture
def client(fake_session):
    return MetzlerClient(fake_session, HOST, b"\x01" * 32)


async def test_search_sends_pinned_https_request(fake_session, client):
    fake_session.add(page([info()]))
    records = await client.async_search_valid_card_events(START, END)
    assert records == [REC]
    call = fake_session.calls[0]
    assert call.method == "POST"
    assert call.url == "https://192.0.2.10/ISAPI/AccessControl/AcsEvent?format=json"
    assert isinstance(call.kwargs["ssl"], aiohttp.Fingerprint)
    assert call.kwargs["ssl"].fingerprint == b"\x01" * 32
    assert call.kwargs["timeout"].total == 5
    cond = call.kwargs["json"]["AcsEventCond"]
    assert (cond["major"], cond["minor"], cond["maxResults"], cond["searchResultPosition"]) == (5, 1, 10, 0)
    assert cond["startTime"] == "2026-11-25T08:56:00+01:00"
    assert cond["endTime"] == "2026-11-25T23:59:59+01:00"
    assert len(cond["searchID"]) == 32


async def test_search_follows_more_paging(fake_session, client):
    fake_session.add(page([info(time=f"2026-11-25 08:57:{i:02d}") for i in range(10)], "MORE", 13))
    fake_session.add(page([info(time=f"2026-11-25 08:58:{i:02d}") for i in range(3)], "OK", 13))
    records = await client.async_search_valid_card_events(START, END)
    calls = fake_session.calls
    assert len(records) == 13
    assert [c.kwargs["json"]["AcsEventCond"]["searchResultPosition"] for c in calls] == [0, 10]
    ids = {c.kwargs["json"]["AcsEventCond"]["searchID"] for c in calls}
    assert len(ids) == 1


async def test_search_uses_fresh_search_id_per_search(fake_session, client):
    fake_session.add(NO_MATCH)
    fake_session.add(NO_MATCH)
    await client.async_search_valid_card_events(START, END)
    await client.async_search_valid_card_events(START, END)
    a, b = (c.kwargs["json"]["AcsEventCond"]["searchID"] for c in fake_session.calls)
    assert a != b


async def test_search_aborts_after_max_pages(fake_session, client):
    for _ in range(12):
        fake_session.add(page([info() for _ in range(10)], "MORE", 999))
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)
    assert len(fake_session.calls) == 10


async def test_more_without_matches_raises(fake_session, client):
    fake_session.add(page([], "MORE", 5))
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)
    assert len(fake_session.calls) == 1


async def test_naive_datetime_rejected(fake_session, client):
    with pytest.raises(ValueError):
        await client.async_search_valid_card_events(datetime(2026, 11, 25, 8), END)
    with pytest.raises(ValueError):
        await client.async_search_valid_card_events(START, datetime(2026, 11, 26))
    assert fake_session.calls == []


async def test_no_matches_without_infolist_is_empty(fake_session, client):
    fake_session.add(NO_MATCH)
    assert await client.async_search_valid_card_events(START, END) == []


async def test_record_without_employee_no_is_skipped(fake_session, client):
    fake_session.add(
        page([info(omit_employee_no=True), info(""), info("  "), info("1001")])
    )
    assert await client.async_search_valid_card_events(START, END) == [REC]


async def test_record_with_wrong_minor_is_skipped(fake_session, client):
    fake_session.add(page([info(minor=38)]))
    assert await client.async_search_valid_card_events(START, END) == []


async def test_record_with_bad_door_or_time_is_skipped(fake_session, client):
    fake_session.add(page([info(door_no="1"), info(door_no=True), info(time=""), info()]))
    assert await client.async_search_valid_card_events(START, END) == [REC]


@pytest.mark.parametrize("body", ["<html>error</html>", "{}", '{"AcsEvent": []}'])
async def test_malformed_response_raises(fake_session, client, body):
    fake_session.add(body)
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ((401, ""), MetzlerAuthError),
        ((403, ""), MetzlerAuthError),
        ((500, ""), MetzlerConnectionError),
        ((404, ""), MetzlerNotSupportedError),
        ((400, ""), MetzlerResponseError),
        (TimeoutError(), MetzlerConnectionError),
        (aiohttp.ClientConnectionError(), MetzlerConnectionError),
        (
            aiohttp.ServerFingerprintMismatch(b"a", b"b", HOST, 443),
            MetzlerCertificateError,
        ),
    ],
)
async def test_error_mapping(fake_session, client, item, expected):
    fake_session.queue.append(item)
    with pytest.raises(expected):
        await client.async_search_valid_card_events(START, END)


async def test_pii_never_leaves_client(fake_session, client, caplog):
    caplog.set_level(logging.DEBUG)
    fake_session.add(page([info()]))
    records = await client.async_search_valid_card_events(START, END)
    assert "PLACEHOLDER" not in repr(records)
    assert "PLACEHOLDER" not in caplog.text

    for body, status in (
        ('{"AcsEvent": {"InfoList": "PLACEHOLDER_CARD PLACEHOLDER_NAME"}}', 200),
        ("not json PLACEHOLDER_CARD PLACEHOLDER_NAME", 200),
        ("PLACEHOLDER_CARD PLACEHOLDER_NAME", 500),
        ("PLACEHOLDER_CARD PLACEHOLDER_NAME", 401),
    ):
        fake_session.add(body, status)
        with pytest.raises(Exception) as err:  # noqa: PT011
            await client.async_search_valid_card_events(START, END)
        assert "PLACEHOLDER" not in str(err.value)
        assert "PLACEHOLDER" not in repr(err.value)
    assert "PLACEHOLDER" not in caplog.text


async def test_undecodable_body_maps_to_response_error(fake_session, client):
    raw = b"PLACEHOLDER_CARD PLACEHOLDER_NAME \xe4"
    for exc in (
        UnicodeDecodeError("utf-8", raw, 33, 34, "invalid"),
        LookupError("unknown encoding: PLACEHOLDER_NAME"),
    ):
        fake_session.queue.append((200, exc))
        with pytest.raises(MetzlerResponseError) as err:
            await client.async_search_valid_card_events(START, END)
        assert "PLACEHOLDER" not in repr(err.value)
        assert "PLACEHOLDER" not in str(err.value)


def test_parse_fingerprint():
    assert parse_fingerprint("ab:CD " * 16) == bytes.fromhex("abcd" * 16)
    for bad in ("", "abcd", "zz" * 32):
        pytest.raises(ValueError, parse_fingerprint, bad)
    assert parse_fingerprint(format_fingerprint(b"\x01" * 32)) == b"\x01" * 32
    assert format_fingerprint(b"\xab\xcd") == "AB:CD"


async def test_device_time(fake_session, client):
    fake_session.add("<Time><localTime>2026-11-25T08:57:25+01:00</localTime></Time>")
    result = await client.async_get_device_time(UTC)
    assert result == datetime(2026, 11, 25, 8, 57, 25, tzinfo=CET)
    assert result.utcoffset() == timedelta(hours=1)
    call = fake_session.calls[0]
    assert call.method == "GET"
    assert call.url == "https://192.0.2.10/ISAPI/System/time"
    assert isinstance(call.kwargs["ssl"], aiohttp.Fingerprint)


async def test_device_time_without_offset_uses_default_tz(fake_session, client):
    fake_session.add('<Time xmlns="http://www.hikvision.com/ver20/XMLSchema"><localTime>2026-11-25T08:57:25</localTime></Time>')
    result = await client.async_get_device_time(CET)
    assert result.tzinfo is CET
    assert result.hour == 8


async def test_device_time_errors(fake_session, client):
    fake_session.add("", 404)
    with pytest.raises(MetzlerNotSupportedError):
        await client.async_get_device_time(UTC)
    for body in ("<Time></Time>", "not xml", "<Time><localTime>x</localTime></Time>"):
        fake_session.add(body)
        with pytest.raises(MetzlerResponseError):
            await client.async_get_device_time(UTC)


async def test_fetch_fingerprint():
    import hashlib

    der = b"fake-der-certificate"
    ssl_obj = MagicMock()
    ssl_obj.getpeercert.return_value = der
    writer = MagicMock()
    writer.get_extra_info.return_value = ssl_obj
    writer.wait_closed = AsyncMock()
    opener = AsyncMock(return_value=(MagicMock(), writer))
    with patch("custom_components.metzler_vdm.api.asyncio.open_connection", opener):
        assert await async_fetch_fingerprint(HOST) == hashlib.sha256(der).digest()
    ssl_obj.getpeercert.assert_called_once_with(binary_form=True)
    writer.get_extra_info.assert_called_once_with("ssl_object")
    writer.close.assert_called_once()
    writer.write.assert_not_called()
    assert opener.call_args.args[:2] == (HOST, 443)


async def test_fetch_fingerprint_connection_error():
    opener = AsyncMock(side_effect=OSError("boom"))
    with patch("custom_components.metzler_vdm.api.asyncio.open_connection", opener):
        with pytest.raises(MetzlerConnectionError):
            await async_fetch_fingerprint(HOST)


@pytest.mark.parametrize(
    "event",
    [
        {"responseStatusStrg": "FAILED"},
        {"responseStatusStrg": "FAILED", "InfoList": []},
        {"responseStatusStrg": "ok", "InfoList": []},
        {"responseStatusStrg": 1, "InfoList": []},
        {"responseStatusStrg": None},
        {"InfoList": []},
        {},
    ],
)
async def test_unknown_or_missing_status_raises(fake_session, client, event):
    fake_session.add(json.dumps({"AcsEvent": event}))
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)


@pytest.mark.parametrize("infos", ["", 0, False, {}, "x", {"a": 1}, None])
@pytest.mark.parametrize("status", ["OK", "NO MATCH", "MORE"])
async def test_non_list_infolist_raises(fake_session, client, status, infos):
    fake_session.add(
        json.dumps(
            {
                "AcsEvent": {
                    "responseStatusStrg": status,
                    "numOfMatches": 1,
                    "InfoList": infos,
                }
            }
        )
    )
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)


async def test_ok_without_infolist_is_empty(fake_session, client):
    fake_session.add(json.dumps({"AcsEvent": {"responseStatusStrg": "OK"}}))
    assert await client.async_search_valid_card_events(START, END) == []


async def test_unexpected_status_logged_sanitised(fake_session, client, caplog):
    caplog.set_level(logging.DEBUG, logger="custom_components.metzler_vdm")
    status = "FAIL\x1b\n\x00ED" + "X" * 60
    fake_session.add(
        json.dumps(
            {"AcsEvent": {"responseStatusStrg": status, "InfoList": [info()]}}
        )
    )
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)
    expected = ("FAILED" + "X" * 60)[:32]
    assert repr(expected) in caplog.text or expected in caplog.text
    assert "X" * 33 not in caplog.text
    assert "\x1b" not in caplog.text
    assert "\x00" not in caplog.text
    assert "PLACEHOLDER" not in caplog.text

    caplog.clear()
    fake_session.add(
        json.dumps({"AcsEvent": {"responseStatusStrg": {"x": "PLACEHOLDER_NAME"}}})
    )
    with pytest.raises(MetzlerResponseError):
        await client.async_search_valid_card_events(START, END)
    assert "PLACEHOLDER" not in caplog.text


async def test_redirects_are_not_followed(fake_session, client):
    fake_session.add(NO_MATCH)
    await client.async_search_valid_card_events(START, END)
    fake_session.add("<Time><localTime>2026-11-25T08:57:25+01:00</localTime></Time>")
    await client.async_get_device_time(UTC)
    assert [c.kwargs.get("allow_redirects") for c in fake_session.calls] == [
        False,
        False,
    ]
    for status in (301, 302, 307, 308):
        fake_session.add("", status)
        with pytest.raises(MetzlerResponseError):
            await client.async_search_valid_card_events(START, END)

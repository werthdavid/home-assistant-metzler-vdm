"""Tests for the HA-free unlock tracker."""

import pytest

from custom_components.metzler_vdm.tracker import (
    AcsRecord,
    AllowlistError,
    Unlock,
    UnlockTracker,
    format_allowlist,
    parse_allowlist,
)

R = AcsRecord("2026-11-25 08:57:25", "1001", 1)
ALLOW = {"1001": "Alias A"}


def test_first_poll_only_primes():
    t = UnlockTracker(ALLOW)
    assert not t.primed
    assert t.process([R], now=0) == []
    assert t.primed
    assert t.process([R], now=2) == []


def test_first_poll_empty_then_new_record_fires_once():
    t = UnlockTracker(ALLOW)
    t.process([], now=0)
    assert t.process([R], now=2) == [
        Unlock("1001", "Alias A", 1, "2026-11-25 08:57:25")
    ]
    assert t.process([R], now=4) == []


def test_unknown_id_is_dropped_and_remembered():
    t = UnlockTracker(ALLOW)
    t.process([], now=0)
    unknown = AcsRecord("2026-11-25 08:57:25", "9999", 1)
    assert t.process([unknown], now=2) == []
    assert t.process([unknown], now=4) == []


def test_duplicate_within_one_batch_fires_once():
    t = UnlockTracker(ALLOW)
    t.process([], now=0)
    assert len(t.process([R, R], now=2)) == 1


def test_same_second_different_id_or_door_both_fire():
    t = UnlockTracker({"1001": "A", "1002": "B"})
    t.process([], now=0)
    recs = [R, AcsRecord(R.time, "1002", 1), AcsRecord(R.time, "1001", 2)]
    assert len(t.process(recs, now=2)) == 3


def test_seen_key_kept_while_still_reported():
    t = UnlockTracker(ALLOW, seen_ttl=300)
    t.process([], now=0)
    t.process([R], now=2)
    for now in range(4, 400, 2):
        assert t.process([R], now=now) == []


def test_seen_key_pruned_after_ttl_without_sighting():
    t = UnlockTracker(ALLOW, seen_ttl=300)
    t.process([], now=0)
    t.process([R], now=2)
    t.process([], now=303)
    # Only demonstrates pruning; in reality R is outside the 60 s window by then.
    assert t.process([R], now=304) != []


def test_parse_allowlist():
    assert parse_allowlist(" 1001 = Alias A \n\n0101\n") == {
        "1001": "Alias A",
        "0101": "0101",
    }
    with pytest.raises(AllowlistError):
        parse_allowlist("1001=A\n1001=B")
    assert parse_allowlist(format_allowlist({"1": "x"})) == {"1": "x"}


def test_ids_are_compared_as_exact_strings():
    t = UnlockTracker({"0101": "A"})
    t.process([], now=0)
    assert t.process([AcsRecord("2026-11-25 08:57:25", "101", 1)], now=1) == []
    assert len(t.process([AcsRecord("2026-11-25 08:57:25", "0101", 1)], now=2)) == 1

"""Checks of user-facing texts."""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "custom_components" / "metzler_vdm"


def load(name: str) -> dict:
    return json.loads((BASE / name).read_text(encoding="utf-8"))


def test_strings_and_en_identical():
    assert load("strings.json") == load("translations/en.json")


def test_certificate_issue_points_to_reconfigure():
    en = load("strings.json")["issues"]["certificate_mismatch"]["description"]
    de = load("translations/de.json")["issues"]["certificate_mismatch"]["description"]
    assert "Reconfigure" in en
    assert "web UI" in en
    assert "attack" in en
    assert "remove" not in en.lower()
    assert "Neu konfigurieren" in de
    assert "Web-Oberfläche" in de
    assert "Angriff" in de
    assert "entferne" not in de.lower()


def test_allowlist_texts_name_the_person_id_not_the_card():
    for name, marker, card in (
        ("strings.json", "employee", "card number"),
        ("translations/de.json", "Personen", "Kartennummer"),
    ):
        data = load(name)
        for step in (
            data["config"]["step"]["access"],
            data["options"]["step"]["init"],
        ):
            text = step["description"] + step["data_description"]["allowlist"]
            assert marker in text, name
            assert card in text, name  # explicitly "not the card number"
            assert "card ID" not in text and "Karten-ID" not in text, name


def test_allow_all_option_is_described_in_all_languages():
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        data = load(name)
        for step in (data["config"]["step"]["access"], data["options"]["step"]["init"]):
            assert step["data"]["allow_all"]
            assert step["data_description"]["allow_all"]
    de = load("translations/de.json")["options"]["step"]["init"]["data_description"]
    assert "ignoriert" in de["allow_all"]

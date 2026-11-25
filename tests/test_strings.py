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

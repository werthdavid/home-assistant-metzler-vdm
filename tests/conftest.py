"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in all tests."""
    yield


@pytest.fixture
def fake_session():
    """Return a fake aiohttp session recording calls."""
    from .fixtures import FakeSession

    return FakeSession()

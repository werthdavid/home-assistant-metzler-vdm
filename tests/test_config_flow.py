"""Tests for the config, options, reauth and reconfigure flows."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
import voluptuous as vol
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.metzler_vdm.api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerConnectionError,
    MetzlerResponseError,
)
from custom_components.metzler_vdm.const import (
    CONF_ALLOWLIST,
    CONF_DOORS,
    CONF_FINGERPRINT,
    CONF_TIMEZONE,
    DOMAIN,
)

RAW_FINGERPRINT = bytes.fromhex("01" * 32)
FINGERPRINT_TEXT = ":".join(["01"] * 32)
HOST = "192.0.2.10"
USER_INPUT = {
    CONF_HOST: HOST,
    CONF_USERNAME: "test-user",
    CONF_PASSWORD: "test-password",
}
ACCESS_INPUT = {
    CONF_ALLOWLIST: "1001=Alias A",
    CONF_TIMEZONE: "Europe/Berlin",
    CONF_DOORS: "1",
}


@pytest.fixture
def fetch_fingerprint():
    """Patch the unverified certificate fetch where the flow looks it up."""
    with patch(
        "custom_components.metzler_vdm.config_flow.async_fetch_fingerprint",
        new=AsyncMock(return_value=RAW_FINGERPRINT),
    ) as mock:
        yield mock


@pytest.fixture
def flow_client():
    """Patch the pinned client where the flow looks it up."""
    with patch(
        "custom_components.metzler_vdm.config_flow.MetzlerClient", autospec=True
    ) as client_cls:
        client = client_cls.return_value
        client.async_search_valid_card_events = AsyncMock(return_value=[])
        yield client_cls


@pytest.fixture
def mock_setup_entry():
    """Skip the real setup after an entry was created."""
    with patch(
        "custom_components.metzler_vdm.async_setup_entry", return_value=True
    ) as mock:
        yield mock


async def to_fingerprint_step(hass: HomeAssistant, user_input=None):
    """Run the user step and return the next form result."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input or USER_INPUT
    )


async def test_full_flow_creates_entry(
    hass, fetch_fingerprint, flow_client, mock_setup_entry
):
    result = await to_fingerprint_step(hass)
    assert result["step_id"] == "fingerprint"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["step_id"] == "access"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], ACCESS_INPUT
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_HOST: HOST,
        CONF_USERNAME: "test-user",
        CONF_PASSWORD: "test-password",
        CONF_FINGERPRINT: "01" * 32,
    }
    assert result["options"] == {
        CONF_ALLOWLIST: {"1001": "Alias A"},
        CONF_TIMEZONE: "Europe/Berlin",
        CONF_DOORS: [1],
    }
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == HOST
    # The probe used the pinned fingerprint.
    assert flow_client.call_args.args[1:] == (HOST, RAW_FINGERPRINT)


async def test_fingerprint_must_be_confirmed(hass, fetch_fingerprint, flow_client):
    result = await to_fingerprint_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "fingerprint"
    assert result["errors"] == {"base": "not_confirmed"}
    # No credentials were used before confirmation.
    flow_client.assert_not_called()


async def test_fingerprint_is_shown(hass, fetch_fingerprint, flow_client):
    result = await to_fingerprint_step(hass)
    assert result["step_id"] == "fingerprint"
    assert result["description_placeholders"]["fingerprint"] == FINGERPRINT_TEXT
    assert "test-password" not in str(result["description_placeholders"])
    flow_client.assert_not_called()


@pytest.mark.parametrize(
    ("exc", "error"),
    [
        (MetzlerAuthError, "invalid_auth"),
        (MetzlerConnectionError, "cannot_connect"),
        (MetzlerCertificateError, "certificate_changed"),
        (MetzlerResponseError, "unexpected_response"),
    ],
)
async def test_probe_errors(hass, fetch_fingerprint, flow_client, exc, error):
    flow_client.return_value.async_search_valid_card_events.side_effect = exc("x")
    result = await to_fingerprint_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "fingerprint"
    assert result["errors"] == {"base": error}
    assert "test-password" not in str(result)


async def test_fetch_fingerprint_failure(hass, fetch_fingerprint, flow_client):
    fetch_fingerprint.side_effect = OSError("down")
    result = await to_fingerprint_step(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}

    fetch_fingerprint.side_effect = MetzlerConnectionError("down")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.parametrize(
    "host",
    ["https://192.0.2.10", "192.0.2.10:443", "192.0.2.10/path", "192 .0.2.10", "", "  "],
)
async def test_invalid_host(hass, fetch_fingerprint, host):
    result = await to_fingerprint_step(hass, {**USER_INPUT, CONF_HOST: host})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "invalid_host"}
    fetch_fingerprint.assert_not_called()


async def test_host_is_stripped(hass, fetch_fingerprint, flow_client):
    result = await to_fingerprint_step(hass, {**USER_INPUT, CONF_HOST: "  192.0.2.10 "})
    assert result["step_id"] == "fingerprint"
    fetch_fingerprint.assert_awaited_once_with(HOST)


async def test_invalid_allowlist_and_doors(
    hass, fetch_fingerprint, flow_client, mock_setup_entry
):
    result = await to_fingerprint_step(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": True}
    )
    flow_id = result["flow_id"]

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**ACCESS_INPUT, CONF_ALLOWLIST: "1001=A\n1001=B"}
    )
    assert result["step_id"] == "access"
    assert result["errors"] == {CONF_ALLOWLIST: "invalid_allowlist"}

    for doors in ("0", "a", "", "1,,2", "-1", "1.5"):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {**ACCESS_INPUT, CONF_DOORS: doors}
        )
        assert result["step_id"] == "access"
        assert result["errors"] == {CONF_DOORS: "invalid_doors"}, doors

    # Duplicates are removed and the result is sorted; empty allowlist is allowed.
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**ACCESS_INPUT, CONF_ALLOWLIST: "", CONF_DOORS: " 2, 1 ,2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"][CONF_DOORS] == [1, 2]
    assert result["options"][CONF_ALLOWLIST] == {}


async def test_duplicate_host_aborts(
    hass, config_entry, fetch_fingerprint, flow_client
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.0.2.10"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    fetch_fingerprint.assert_not_called()


async def test_options_flow_updates_allowlist_and_reloads(
    hass, config_entry, mock_client
):
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    old_coordinator = config_entry.runtime_data

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ALLOWLIST: "1001=Alias A\n1002=Alias B",
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_DOORS: "1, 2",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert config_entry.options == {
        CONF_ALLOWLIST: {"1001": "Alias A", "1002": "Alias B"},
        CONF_TIMEZONE: "Europe/Berlin",
        CONF_DOORS: [1, 2],
    }
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data is not old_coordinator


async def test_options_flow_is_prefilled_and_validates(hass, config_entry, mock_client):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_ALLOWLIST].description == {"suggested_value": "1001=Alias A"}
    assert keys[CONF_TIMEZONE].default() == "Europe/Berlin"
    assert keys[CONF_DOORS].description == {"suggested_value": "1"}
    # Stored values are suggestions only, never defaults.
    assert keys[CONF_ALLOWLIST].default is vol.UNDEFINED
    assert keys[CONF_DOORS].default is vol.UNDEFINED

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**ACCESS_INPUT, CONF_DOORS: "x"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DOORS: "invalid_doors"}


async def test_options_flow_omitted_allowlist_empties_it(
    hass, config_entry, mock_client
):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TIMEZONE: "Europe/Berlin", CONF_DOORS: "1"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_ALLOWLIST] == {}


async def test_options_flow_omitted_doors_is_invalid(hass, config_entry, mock_client):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_ALLOWLIST: "1001=Alias A", CONF_TIMEZONE: "Europe/Berlin"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_DOORS: "invalid_doors"}
    assert config_entry.options[CONF_DOORS] == [1]


async def test_reauth_updates_password(hass, config_entry, mock_client, flow_client):
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-password"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "new-password"
    assert config_entry.data[CONF_USERNAME] == "test-user"
    assert config_entry.data[CONF_FINGERPRINT] == "01" * 32
    # Probe used the stored pinned fingerprint.
    assert flow_client.call_args.args[1:] == (HOST, RAW_FINGERPRINT)


async def test_reauth_wrong_password_shows_error(
    hass, config_entry, mock_client, flow_client
):
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    flow_client.return_value.async_search_valid_card_events.side_effect = (
        MetzlerAuthError("x")
    )
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_PASSWORD] == "test-password"


async def test_reconfigure_updates_fingerprint_keeps_options(
    hass, config_entry, mock_client, fetch_fingerprint, flow_client
):
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    options_before = dict(config_entry.options)
    fetch_fingerprint.return_value = bytes.fromhex("02" * 32)

    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "fingerprint"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_FINGERPRINT] == "02" * 32
    assert config_entry.options == options_before
    # The probe pinned the new fingerprint.
    assert flow_client.call_args.args[1:] == (HOST, bytes.fromhex("02" * 32))


async def test_reconfigure_to_existing_host_aborts(
    hass, config_entry, fetch_fingerprint, flow_client
):
    MockConfigEntry(domain=DOMAIN, unique_id="192.0.2.11", data={}).add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, CONF_HOST: "192.0.2.11"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_auth_error_starts_reauth(hass, config_entry, mock_client, freezer):
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_search_valid_card_events.side_effect = MetzlerAuthError("bad")
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert any(
        flow["context"]["source"] == "reauth"
        and flow["context"]["entry_id"] == config_entry.entry_id
        for flow in flows
    )


async def test_probe_session_registers_no_cleanup_listener(
    hass, fetch_fingerprint, flow_client, mock_setup_entry, caplog
):
    from homeassistant.const import EVENT_HOMEASSISTANT_CLOSE

    def close_listeners() -> int:
        return hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_CLOSE, 0)

    # First probe warms up shared helpers (connector) that listen once.
    result = await to_fingerprint_step(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert flow_client.call_args.args[0].closed
    before = close_listeners()

    result = await to_fingerprint_step(hass, {**USER_INPUT, CONF_HOST: "192.0.2.11"})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"confirm": True})
    assert flow_client.call_args.args[0].closed
    assert close_listeners() == before
    assert "closes the Home Assistant aiohttp session" not in caplog.text

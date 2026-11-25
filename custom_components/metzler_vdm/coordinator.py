"""Coordinator polling the door station for valid unlocks."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerClient,
    MetzlerError,
    MetzlerNotSupportedError,
)
from .const import (
    CONF_ALLOWLIST,
    CONF_DOORS,
    CONF_TIMEZONE,
    DOMAIN,
    DRIFT_CHECK_INTERVAL,
    DRIFT_THRESHOLD,
    END_AHEAD_HOURS,
    POLL_INTERVAL,
    WINDOW_SECONDS,
)
from .tracker import Unlock, UnlockTracker

_LOGGER = logging.getLogger(__name__)

type MetzlerConfigEntry = ConfigEntry[MetzlerCoordinator]

ISSUE_CERTIFICATE = "certificate_mismatch"
ISSUE_CLOCK_DRIFT = "clock_drift"


class MetzlerCoordinator(DataUpdateCoordinator[list[Unlock]]):
    """Poll valid-card events and return the new allowlisted unlocks."""

    config_entry: MetzlerConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: MetzlerConfigEntry, client: MetzlerClient
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self._client = client
        self._tz = ZoneInfo(entry.options[CONF_TIMEZONE])
        self._doors: list[int] = list(entry.options[CONF_DOORS])
        self._tracker = UnlockTracker(entry.options[CONF_ALLOWLIST])
        self._drift_supported = True
        self._next_drift_check = 0.0
        self.clock_drift: float | None = None

    @property
    def tracker_primed(self) -> bool:
        """Return whether the first successful poll has been processed."""
        return self._tracker.primed

    def issue_id(self, key: str) -> str:
        """Return the per-entry issue id for a translation key."""
        return f"{key}_{self.config_entry.entry_id}"

    async def _async_update_data(self) -> list[Unlock]:
        """Fetch records; fire nothing unless everything succeeded."""
        now = datetime.now(self._tz)
        try:
            records = await self._client.async_search_valid_card_events(
                now - timedelta(seconds=WINDOW_SECONDS),
                now + timedelta(hours=END_AHEAD_HOURS),
            )
        except MetzlerCertificateError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id(ISSUE_CERTIFICATE),
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_CERTIFICATE,
            )
            raise UpdateFailed(str(err)) from err
        except MetzlerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MetzlerError as err:
            raise UpdateFailed(str(err)) from err

        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id(ISSUE_CERTIFICATE))
        unlocks = self._tracker.process(records, time.monotonic())
        for unlock in unlocks:
            if unlock.door_no not in self._doors:
                _LOGGER.debug("Ignoring unlock at unconfigured door %s", unlock.door_no)
        await self._async_check_clock()
        return unlocks

    async def _async_check_clock(self) -> None:
        """Compare the station clock with Home Assistant; never fails the poll."""
        if not self._drift_supported:
            return
        monotonic = time.monotonic()
        if monotonic < self._next_drift_check:
            return
        try:
            device_time = await self._client.async_get_device_time(self._tz)
        except MetzlerNotSupportedError:
            _LOGGER.debug("Device time not supported; clock check disabled")
            self._drift_supported = False
            self.clock_drift = None
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id(ISSUE_CLOCK_DRIFT))
            return
        except Exception as err:  # noqa: BLE001 - must never fail the poll
            _LOGGER.debug("Device time query failed (%s)", type(err).__name__)
            return

        self._next_drift_check = monotonic + DRIFT_CHECK_INTERVAL
        drift = (device_time - dt_util.utcnow()).total_seconds()
        self.clock_drift = drift
        if abs(drift) >= DRIFT_THRESHOLD:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.issue_id(ISSUE_CLOCK_DRIFT),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_CLOCK_DRIFT,
                translation_placeholders={"seconds": str(round(abs(drift)))},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id(ISSUE_CLOCK_DRIFT))

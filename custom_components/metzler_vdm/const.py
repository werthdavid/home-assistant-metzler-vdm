"""Constants for the Metzler VDM integration."""

DOMAIN = "metzler_vdm"

POLL_INTERVAL = 2
WINDOW_SECONDS = 60
END_AHEAD_HOURS = 24
PAGE_SIZE = 10
MAX_PAGES = 10
REQUEST_TIMEOUT = 5
SEEN_TTL = 7200  # covers the repeated hour at the end of DST
DRIFT_THRESHOLD = 10
DRIFT_CHECK_INTERVAL = 3600
DRIFT_RETRY_INTERVAL = 60

MAJOR = 5
MINOR_VALID_CARD = 1

CONF_FINGERPRINT = "fingerprint"
CONF_ALLOWLIST = "allowlist"
CONF_TIMEZONE = "timezone"
CONF_DOORS = "doors"

EVENT_VALID_UNLOCK = "metzler_vdm_valid_unlock"

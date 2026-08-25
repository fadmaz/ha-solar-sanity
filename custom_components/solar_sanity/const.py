"""Constants for Solar Sanity."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "solar_sanity"
PLATFORMS: Final = ["sensor", "binary_sensor"]

#: Bumped only on a breaking change to the WebSocket schema. This — not the
#: integration's version — is what the card checks compatibility against, so a
#: cosmetic version difference never nags the user.
API_VERSION: Final = 1

URL_BASE: Final = "/solar_sanity"
CARD_FILENAME: Final = "solar-sanity.js"

# --- configuration keys ---------------------------------------------------
CONF_CHANNELS: Final = "channels"
CONF_ROLE: Final = "role"
CONF_ENTITY_ID: Final = "entity_id"
CONF_ORIGIN: Final = "origin"

CONF_HAS_BATTERY: Final = "has_battery"
CONF_GRID_IS_NET: Final = "grid_is_net"
CONF_LOAD_WHOLE_HOUSE: Final = "load_whole_house"

CONF_FORECAST_ENTRIES: Final = "forecast_entries"
CONF_GUARANTEED_ANNUAL_KWH: Final = "guaranteed_annual_kwh"

OPT_CORRECTIONS: Final = "corrections"
OPT_SUPPRESSED: Final = "suppressed_codes"

# --- storage ---------------------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_MINOR_VERSION: Final = 1
STORAGE_KEY_STATE: Final = f"{DOMAIN}.state"

#: Daily digests only. Raw hourly buckets live in memory for the current window;
#: the long history lives in the recorder as external statistics, which is
#: indexed, never purged, and does not cost boot time the way a growing
#: ``.storage`` file would.
DIGEST_RETENTION_DAYS: Final = 400

# --- scheduling ------------------------------------------------------------
#: The live tripwire. Only ever raises simultaneous-flow and stuck/stale.
LIVE_INTERVAL: Final = timedelta(seconds=30)

#: Our own hourly integrator closes a bucket on the hour.
BUCKET_INTERVAL: Final = timedelta(minutes=5)

#: The full analysis. Nightly is plenty — nothing here changes minute to minute.
ANALYSIS_INTERVAL: Final = timedelta(hours=6)

#: Every channel must have updated within this window for a live snapshot to
#: mean anything. Comparing a 60-second-stale battery reading against a
#: 5-second-fresh PV reading produces a kilowatt-scale residual with no fault
#: present, which is why the live tier may not raise inferential findings.
LIVE_MAX_AGE_SECONDS: Final = 120

# --- forecast capture ------------------------------------------------------
#: Home Assistant's own forecast integrations set no ``state_class`` on their
#: energy sensors, so their history lives only in the ``states`` table and is
#: purged after ~10 days. Capturing at issue time is the only way to score a
#: forecast later, and it is the one thing in this product that cannot be
#: backfilled.
FORECAST_CAPTURE_INTERVAL: Final = timedelta(minutes=30)
FORECAST_STATISTIC_PREFIX: Final = f"{DOMAIN}:forecast_"

# --- events ----------------------------------------------------------------
EVENT_FINDING_RAISED: Final = f"{DOMAIN}_finding_raised"
EVENT_FINDING_CLEARED: Final = f"{DOMAIN}_finding_cleared"

# --- services --------------------------------------------------------------
SERVICE_VALIDATE_NOW: Final = "validate_now"
SERVICE_EXPORT_REPORT: Final = "export_report"

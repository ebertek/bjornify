"""Select a controllable Spotify Connect device when no session is active."""

import logging

_LOGGER = logging.getLogger(__name__)


def select_fallback_device(response, preferred_name):
    """Prefer the configured device, otherwise return the first usable device.

    A restricted device rejects Web API commands; a missing device ID cannot
    be targeted when there is no active Spotify playback session. Do not filter
    by type, volume control, or active flag: those are not playability checks.
    """
    available = []
    for device in (response or {}).get("devices") or []:
        if not isinstance(device, dict):
            continue
        if device.get("is_restricted") is True:
            _LOGGER.debug(
                "Skipping restricted Spotify device: %s", device.get("name", "Unknown")
            )
            continue
        if not device.get("id"):
            _LOGGER.debug(
                "Skipping Spotify device without an ID: %s",
                device.get("name", "Unknown"),
            )
            continue
        available.append(device)
    return next(
        (device for device in available if device.get("name") == preferred_name),
        available[0] if available else None,
    )

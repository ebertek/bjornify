"""Classify Spotify device restrictions separately from account permission errors."""


def is_sonos_device_restriction(error):
    """Distinguish a restricted device from unrelated HTTP 403 responses."""
    detail = f"{error.reason or ''} {error.msg or ''}".lower()
    return error.http_status == 403 and (
        "restricted device" in detail or "restriction violated" in detail
    )


def spotify_forbidden_message(error):
    """Give actionable errors without suggesting new OAuth for restricted devices."""
    if is_sonos_device_restriction(error):
        return (
            "Spotify cannot add to this restricted device's queue. "
            "The song was NOT queued; add it using the Spotify app."
        )
    return (
        "Spotify denied access (403). Check Premium, Development Mode "
        "allowlisting and app permissions."
    )

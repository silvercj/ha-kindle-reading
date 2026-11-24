"""Diagnostics for Kindle Reading (no cookies or tokens)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import KindleConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: KindleConfigEntry
) -> dict[str, Any]:
    """Return reading state without credentials."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "region": entry.data.get("region"),
        "cookie_names": sorted(entry.data.get("cookies", {})),
        "has_device_token": bool(entry.data.get("device_token")),
        "options": dict(entry.options),
        "state": await coordinator.async_state_snapshot(),
        "recent": [b.as_dict() for b in data.recent] if data else None,
    }

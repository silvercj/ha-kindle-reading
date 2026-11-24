"""Kindle Reading: Whispersync reading progress in Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import KindleClient
from .const import CONF_COOKIES, CONF_DEVICE_TOKEN, CONF_REGION, REGIONS
from .coordinator import KindleCoordinator

PLATFORMS = [Platform.SENSOR]

type KindleConfigEntry = ConfigEntry[KindleCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: KindleConfigEntry) -> bool:
    """Set up Kindle Reading from a config entry."""
    client = KindleClient(
        REGIONS[entry.data[CONF_REGION]],
        entry.data[CONF_COOKIES],
        entry.data[CONF_DEVICE_TOKEN],
    )
    coordinator = KindleCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.async_close()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KindleConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.async_close()
    return unloaded

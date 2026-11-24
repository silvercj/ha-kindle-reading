"""Kindle Reading sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import KindleConfigEntry
from .const import DOMAIN
from .coordinator import KindleCoordinator, KindleData


@dataclass(frozen=True, kw_only=True)
class KindleSensorDescription(SensorEntityDescription):
    """Sensor description with value and attribute getters."""

    value_fn: Callable[[KindleData], Any]
    attrs_fn: Callable[[KindleData], dict[str, Any] | None] = lambda _: None
    picture_fn: Callable[[KindleData], str | None] = lambda _: None


def _current(d: KindleData, key: str) -> Any:
    return getattr(d.current, key) if d.current else None


SENSORS: tuple[KindleSensorDescription, ...] = (
    KindleSensorDescription(
        key="current_book",
        translation_key="current_book",
        icon="mdi:book-open-page-variant",
        value_fn=lambda d: d.current.title[:255] if d.current else None,
        attrs_fn=lambda d: {
            **(d.current.as_dict() if d.current else {}),
            "recent_books": [b.as_dict() for b in d.recent[:5]],
        },
        picture_fn=lambda d: _current(d, "cover_url"),
    ),
    KindleSensorDescription(
        key="current_book_progress",
        translation_key="current_book_progress",
        icon="mdi:book-clock",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _current(d, "percent"),
        attrs_fn=lambda d: {"title": _current(d, "title")},
    ),
    KindleSensorDescription(
        key="last_read",
        translation_key="last_read",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _current(d, "last_read"),
    ),
    KindleSensorDescription(
        key="last_read_on",
        translation_key="last_read_on",
        icon="mdi:tablet",
        value_fn=lambda d: _current(d, "device"),
    ),
    KindleSensorDescription(
        key="reading_time_today",
        translation_key="reading_time_today",
        icon="mdi:timer-sand",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda d: d.reading_minutes_today,
        attrs_fn=lambda d: {"estimated": True, "books": d.books_today},
    ),
    KindleSensorDescription(
        key="progress_today",
        translation_key="progress_today",
        icon="mdi:book-arrow-right",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda d: d.progress_today,
        attrs_fn=lambda d: {"books": d.books_today},
    ),
    KindleSensorDescription(
        key="books_finished_this_year",
        translation_key="books_finished_this_year",
        icon="mdi:bookshelf",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: len(d.finished_this_year),
        attrs_fn=lambda d: {"books": d.finished_this_year},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KindleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors."""
    coordinator = entry.runtime_data
    async_add_entities(KindleSensor(coordinator, entry, desc) for desc in SENSORS)


class KindleSensor(CoordinatorEntity[KindleCoordinator], SensorEntity):
    """One Kindle Reading sensor."""

    _attr_has_entity_name = True
    entity_description: KindleSensorDescription

    def __init__(
        self,
        coordinator: KindleCoordinator,
        entry: KindleConfigEntry,
        description: KindleSensorDescription,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Kindle",
            manufacturer="Amazon",
            model="Whispersync (Cloud Reader)",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> Any:
        """State."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Attributes."""
        return self.entity_description.attrs_fn(self.coordinator.data)

    @property
    def entity_picture(self) -> str | None:
        """Book cover."""
        return self.entity_description.picture_fn(self.coordinator.data)

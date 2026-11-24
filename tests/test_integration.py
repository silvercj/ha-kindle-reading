"""Tests with Amazon mocked out."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.kindle_reading.api import (
    BookProgress,
    KindleAuthError,
    KindleClient,
    LibraryBook,
    parse_cookie_header,
    parse_device_token,
    _authors,
)
from custom_components.kindle_reading.const import DOMAIN

COOKIES = "cookie: session-id=123-456; ubid-acbuk=999; at-acbuk=Atza|abc; x-acbuk=xyz; other=1"
TOKEN_URL = "https://read.amazon.co.uk/service/web/register/getDeviceToken?serialNumber=A2CTOKEN&deviceType=A2CTOKEN"


def lib(n=2):
    return [
        LibraryBook("B01", "Book One", ["Ann Author"], "https://img/1.jpg", "PURCHASE", "EBOOK"),
        LibraryBook("B02", "Book Two", ["Bob Writer"], "https://img/2.jpg", "PURCHASE", "EBOOK"),
    ][:n]


class FakeClient:
    def __init__(self):
        self.positions = {"B01": 10000, "B02": 99000}
        self.sync = {
            "B01": datetime(2026, 11, 24, 7, 0, tzinfo=UTC),
            "B02": datetime(2026, 3, 1, 21, 0, tzinfo=UTC),
        }
        self.fail_auth = False

    async def async_get_library(self, count=10):
        if self.fail_auth:
            raise KindleAuthError("x")
        return lib()

    async def async_get_progress(self, asin):
        return BookProgress(asin, self.positions[asin], 0, 100000, self.sync[asin],
                            "Chris's Paperwhite" if asin == "B01" else "Chris's Kindle")

    async def async_register_device(self):
        return None

    def current_cookies(self):
        return {}

    async def async_close(self):
        return None


def test_parsers():
    c = parse_cookie_header(COOKIES)
    assert c["at-acbuk"] == "Atza|abc" and c["session-id"] == "123-456" and "cookie: session-id" not in c
    assert parse_device_token(TOKEN_URL) == "A2CTOKEN"
    assert parse_device_token(" A2CTOKEN ") == "A2CTOKEN"
    assert _authors(["Reynolds, Alastair:Smith, Jo"]) == ["Alastair Reynolds", "Jo Smith"]
    assert BookProgress("x", 500, 0, 1000, None, None).percent == 50.0
    assert BookProgress("x", None, 0, 1000, None, None).percent is None


async def test_config_flow(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    with (
        patch.object(KindleClient, "async_get_library", AsyncMock(return_value=lib(1))),
        patch.object(KindleClient, "async_register_device", AsyncMock()),
        patch("custom_components.kindle_reading.async_setup_entry", AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"region": "uk", "cookies": COOKIES, "device_token": TOKEN_URL}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["device_token"] == "A2CTOKEN"
    assert result["data"]["cookies"]["at-acbuk"] == "Atza|abc"


async def test_config_flow_bad_cookies(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"region": "uk", "cookies": "foo=bar", "device_token": "x"}
    )
    assert result["errors"] == {"cookies": "missing_cookies"}
    with patch.object(KindleClient, "async_get_library", AsyncMock(side_effect=KindleAuthError("x"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"region": "uk", "cookies": COOKIES, "device_token": "x"}
        )
    assert result["errors"] == {"cookies": "invalid_auth"}


async def test_sensors_and_daily_tracking(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    await hass.config.async_set_time_zone("Europe/London")
    freezer.move_to("2026-11-24 08:00:00+00:00")
    fake = FakeClient()
    entry = MockConfigEntry(domain=DOMAIN, data={"region": "uk", "cookies": parse_cookie_header(COOKIES), "device_token": "T"})
    entry.add_to_hass(hass)
    with patch("custom_components.kindle_reading.KindleClient", return_value=fake), \
         patch("custom_components.kindle_reading.coordinator.REQUEST_GAP", 0):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        assert hass.states.get("sensor.kindle_current_book").state == "Book One"
        assert hass.states.get("sensor.kindle_current_book").attributes["entity_picture"] == "https://img/1.jpg"
        assert float(hass.states.get("sensor.kindle_current_book_progress").state) == 10.0
        assert hass.states.get("sensor.kindle_last_read_on").state == "Chris's Paperwhite"
        assert float(hass.states.get("sensor.kindle_reading_time_today").state) == 0
        # Book Two is 99% and synced this year -> finished
        assert hass.states.get("sensor.kindle_books_finished_this_year").state == "1"

        # Read 30,000 positions (= 20 min at 1,500/min) on the other Kindle
        fake.positions["B01"] = 40000
        fake.sync["B01"] = datetime(2026, 11, 24, 8, 20, tzinfo=UTC)
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert float(hass.states.get("sensor.kindle_reading_time_today").state) == 20.0
        assert float(hass.states.get("sensor.kindle_progress_today").state) == 30.0
        assert float(hass.states.get("sensor.kindle_current_book_progress").state) == 40.0

        # Next day resets
        freezer.move_to("2026-11-25 07:00:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert float(hass.states.get("sensor.kindle_reading_time_today").state) == 0

        # Cookies expire -> reauth flow starts
        fake.fail_auth = True
        freezer.tick(timedelta(minutes=31))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        flows = hass.config_entries.flow.async_progress()
        assert any(f["context"]["source"] == "reauth" for f in flows)

"""Config flow for Kindle Reading."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .api import (
    KindleAuthError,
    KindleBlockedError,
    KindleClient,
    KindleDeviceTokenError,
    KindleError,
    has_sign_in_cookies,
    parse_cookie_header,
    parse_device_token,
)
from .const import (
    CONF_BOOKS_TO_CHECK,
    CONF_COOKIES,
    CONF_DEVICE_TOKEN,
    CONF_FINISHED_THRESHOLD,
    CONF_READING_SPEED,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_BOOKS_TO_CHECK,
    DEFAULT_FINISHED_THRESHOLD,
    DEFAULT_READING_SPEED,
    DEFAULT_REGION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
    REGIONS,
)

_LOGGER = logging.getLogger(__name__)

_MULTILINE = TextSelector(TextSelectorConfig(multiline=True))


async def _validate(region: str, cookies: dict[str, str], token: str) -> tuple[dict[str, str], str | None]:
    """Try the library and device calls. Returns (errors, account hint)."""
    errors: dict[str, str] = {}
    if not has_sign_in_cookies(cookies):
        return {CONF_COOKIES: "missing_cookies"}, None
    client = KindleClient(REGIONS[region], cookies, token)
    try:
        books = await client.async_get_library(1)
        if token:
            await client.async_register_device()
        if books:
            # Proves progress can be read (with or without a device token).
            await client.async_get_progress(books[0].asin)
    except KindleAuthError:
        errors[CONF_COOKIES] = "invalid_auth"
    except KindleDeviceTokenError:
        errors[CONF_DEVICE_TOKEN] = "invalid_device_token"
    except KindleBlockedError:
        errors["base"] = "blocked"
    except KindleError:
        errors["base"] = "cannot_connect"
    except Exception:
        _LOGGER.exception("Unexpected error validating Kindle sign-in")
        errors["base"] = "unknown"
    finally:
        await client.async_close()
    ubid = next((v for k, v in cookies.items() if k.startswith("ubid-")), None)
    return errors, ubid


class KindleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup UI."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for region, cookies and device token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            region = user_input[CONF_REGION]
            cookies = parse_cookie_header(user_input[CONF_COOKIES])
            token = parse_device_token(user_input.get(CONF_DEVICE_TOKEN) or "")
            errors, ubid = await _validate(region, cookies, token)
            if not errors:
                await self.async_set_unique_id(f"{region}-{ubid or 'account'}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Kindle ({region.upper()})",
                    data={CONF_REGION: region, CONF_COOKIES: cookies, CONF_DEVICE_TOKEN: token},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_REGION, default=DEFAULT_REGION): SelectSelector(
                    SelectSelectorConfig(
                        options=list(REGIONS),
                        translation_key="region",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_COOKIES): _MULTILINE,
                vol.Optional(CONF_DEVICE_TOKEN): _MULTILINE,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication when cookies expire."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for fresh cookies (and token)."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            cookies = parse_cookie_header(user_input[CONF_COOKIES])
            raw_token = (user_input.get(CONF_DEVICE_TOKEN) or "").strip()
            token = parse_device_token(raw_token) if raw_token else entry.data.get(CONF_DEVICE_TOKEN, "")
            errors, _ = await _validate(entry.data[CONF_REGION], cookies, token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data={**entry.data, CONF_COOKIES: cookies, CONF_DEVICE_TOKEN: token},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_COOKIES): _MULTILINE,
                vol.Optional(CONF_DEVICE_TOKEN): _MULTILINE,
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> KindleOptionsFlow:
        """Options."""
        return KindleOptionsFlow()


class KindleOptionsFlow(OptionsFlowWithReload):
    """Polling and estimate settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show options."""
        if user_input is not None:
            return self.async_create_entry(data={k: int(v) for k, v in user_input.items()})
        opts = self.config_entry.options

        def num(minimum: int, maximum: int, step: int, unit: str | None = None) -> NumberSelector:
            return NumberSelector(
                NumberSelectorConfig(
                    min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX,
                    unit_of_measurement=unit,
                )
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL, default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): num(MIN_SCAN_INTERVAL, 240, 5, "min"),
                vol.Required(
                    CONF_READING_SPEED, default=opts.get(CONF_READING_SPEED, DEFAULT_READING_SPEED)
                ): num(300, 5000, 50),
                vol.Required(
                    CONF_FINISHED_THRESHOLD,
                    default=opts.get(CONF_FINISHED_THRESHOLD, DEFAULT_FINISHED_THRESHOLD),
                ): num(80, 100, 1, "%"),
                vol.Required(
                    CONF_BOOKS_TO_CHECK, default=opts.get(CONF_BOOKS_TO_CHECK, DEFAULT_BOOKS_TO_CHECK)
                ): num(1, 15, 1),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

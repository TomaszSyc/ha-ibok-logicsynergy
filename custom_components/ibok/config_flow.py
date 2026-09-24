"""Config and options flow for the iBOK integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector
from yarl import URL

from .api import (
    IbokApi,
    IbokAuthError,
    IbokConnectionError,
    IbokError,
    IbokResponseError,
)
from .const import (
    CONF_BASE_URL,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
    MIN_SCAN_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER = vol.Schema(
    {
        vol.Required(CONF_BASE_URL): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
    }
)


class _AddressError(Exception):
    """The portal address cannot be used; the argument is the form error key."""


def normalise_address(raw: str) -> str:
    """Return the portal address in one canonical form, or raise _AddressError.

    HTTPS only: over plain HTTP the password travels unencrypted on every
    login, and the portal's session cookie is marked Secure anyway, so it would
    not work either. A login, query or fragment in the address is refused:
    the address becomes the entry title, and so every device name, and a
    password written into it would end up there.
    """
    text = raw.strip()
    if "://" not in text:
        text = f"https://{text}"
    try:
        url = URL(text)
    except ValueError as err:
        raise _AddressError("invalid_url") from err

    if url.scheme != "https":
        raise _AddressError("insecure_url")
    if not url.host or url.user or url.password or url.query_string or url.fragment:
        raise _AddressError("invalid_url")

    port = f":{url.explicit_port}" if url.explicit_port else ""
    return f"https://{url.host.lower()}{port}{url.path.rstrip('/')}"


async def _async_validate(data: dict[str, Any]) -> None:
    api = IbokApi(data[CONF_BASE_URL], data[CONF_USERNAME], data[CONF_PASSWORD])
    try:
        await api.async_login()
    finally:
        await api.async_close()


class IbokConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the instance address and the account."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = normalise_address(user_input[CONF_BASE_URL])
            except _AddressError as err:
                errors[CONF_BASE_URL] = str(err)
            else:
                user_input[CONF_BASE_URL] = url
                host = URL(url).host
                await self.async_set_unique_id(f"{host}:{user_input[CONF_USERNAME]}")
                self._abort_if_unique_id_configured()

                try:
                    await _async_validate(user_input)
                except IbokAuthError:
                    errors["base"] = "invalid_auth"
                except IbokResponseError:
                    errors["base"] = "not_ibok"
                except IbokConnectionError:
                    errors["base"] = "cannot_connect"
                except IbokError:
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(title=host, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _async_validate(data)
            except IbokAuthError:
                errors["base"] = "invalid_auth"
            except IbokError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return IbokOptionsFlow()


class IbokOptionsFlow(OptionsFlow):
    """Polling interval, plus an optional source entity for each meter."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Dropping empty values is what lets an assignment be removed again:
            # an empty selector would otherwise be stored as "" and the button
            # would keep existing while never having anything to send.
            return self.async_create_entry(
                data={k: v for k, v in user_input.items() if v not in (None, "")}
            )

        options = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=options.get(CONF_SCAN_INTERVAL, 6),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_HOURS,
                    max=24,
                    step=1,
                    unit_of_measurement="h",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
        }

        # One field per meter, named after its serial. A property with a garden
        # sub-meter has two meters billed differently, and feeding both from the
        # same sensor would send the house reading under the garden meter.
        #
        # These keys are built at runtime, so Home Assistant has no translation
        # for them and shows the key itself. That is why the serial is in the
        # key: "source_entity_12345678" still tells you which meter it is.
        for meter in self._meters():
            serial = str(meter.get("numer_fabryczny") or meter.get("id_wodom") or "")
            if not serial:
                continue
            key = f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"
            fields[
                vol.Optional(key, description={"suggested_value": options.get(key)})
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))

        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))

    def _meters(self) -> list[dict[str, Any]]:
        """Meters the portal currently accepts a reading for.

        Empty while the entry is not loaded, in which case the form simply shows
        the interval -- better than refusing to open the options at all.
        """
        coordinator = getattr(self.config_entry, "runtime_data", None)
        if coordinator is None:
            return []
        return coordinator.submittable_meters

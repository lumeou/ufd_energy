from homeassistant import config_entries
from homeassistant.helpers.selector import selector
from typing import Any, Dict, Optional
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
import logging
from .const import DOMAIN
from .ufd import UFD

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = {
    vol.Required('ufd_login'): cv.string,
    vol.Required('ufd_password'): cv.string
}
CUPS_SCHEMA = {
    vol.Required('cups'): cv.string,
    vol.Required('bills_number', default='5'): cv.positive_int,
    vol.Required('has_self_consumption', default=False): cv.boolean
}

MARKET_TARIFF_FIELDS = [
    'market_price_entity', 'power_price_entity',
    'meter_rent_entity', 'social_bonus_entity', 
    'electricity_tax_entity', 'iva_entity', 
    'export_compensation_entity'
]

def market_tariff_schema(defaults=None):
    defaults = defaults or {}
    entity_selector = selector({"entity": {"domain": ["sensor", "input_number"]}})
    return {
        vol.Optional(field, description={"suggested_value": defaults.get(field)}): entity_selector
        for field in MARKET_TARIFF_FIELDS
    }

def options_schema(defaults=None):
    defaults = defaults or {}
    schema = {
        vol.Required('has_self_consumption', default=defaults.get('has_self_consumption', False)): cv.boolean,
    }
    schema.update(market_tariff_schema(defaults))
    return schema

class UfdEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    data: Optional[Dict[str, Any]]

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            _LOGGER.debug(f"ufd_energy: async_step_user, user_input={user_input}")

            UFD.User = user_input['ufd_login']
            UFD.Password = user_input['ufd_password']
            self.data = user_input
            self.data['cups_list'] = await UFD.supplyPoints()
            return await self.async_step_cups()
        
        return self.async_show_form(step_id="user", data_schema=vol.Schema(AUTH_SCHEMA), errors=None)
    
    async def async_step_cups(self, user_input: Optional[Dict[str, Any]] = None):
        CUPS_SCHEMA['cups'] = selector({
            "select": {
                "options": list(self.data['cups_list'].keys()),
            }
        })
        if user_input is not None:
            _LOGGER.debug(f"ufd_energy: async_step_cups, user_input={user_input}")
            supply_point = self.data['cups_list'][user_input['cups']]
            self.data['cups'] = supply_point['cups']
            self.data['zip_code'] = supply_point['zip_code']
            self.data['contract_start_date'] = supply_point['contract_start_date']
            self.data['bills_number'] = user_input['bills_number']
            self.data['has_self_consumption'] = user_input['has_self_consumption']
            del self.data['cups_list']

            return await self.async_step_market_tariff()
        else:
            _LOGGER.debug(f"cups: {list(self.data['cups_list'].keys())}")

        return self.async_show_form(step_id="cups", data_schema=vol.Schema(CUPS_SCHEMA), errors=None)

    async def async_step_market_tariff(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            _LOGGER.debug(f"ufd_energy: async_step_market_tariff, user_input={user_input}")
            self.data.update(user_input)
            return self.async_create_entry(title=self.data['cups'], data=self.data)

        return self.async_show_form(
            step_id="market_tariff",
            data_schema=vol.Schema(market_tariff_schema()),
            errors=None,
            description_placeholders={"info": "Todos los campos son opcionales. Déjalos en blanco si no tienes tarifa de mercado libre o quieres configurarlos más adelante desde 'Configurar'."}
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return UfdEnergyOptionsFlow()


class UfdEnergyOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            _LOGGER.debug(f"ufd_energy: options_flow async_step_init, user_input={user_input}")
            data = {**self.config_entry.data}
            data['has_self_consumption'] = user_input['has_self_consumption']
            for field in MARKET_TARIFF_FIELDS:
                if field in user_input:
                    data[field] = user_input[field]
                else:
                    data.pop(field, None)
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(options_schema(self.config_entry.data)),
            errors=None,
        )

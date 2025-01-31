"""Support for Xiaomi lights."""
import logging
from functools import partial

from homeassistant.const import *  # noqa: F401
from homeassistant.components.light import (
    DOMAIN as ENTITY_DOMAIN,
    LightEntity,
    SUPPORT_BRIGHTNESS,
    SUPPORT_COLOR_TEMP,
    SUPPORT_COLOR,
    SUPPORT_EFFECT,
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_HS_COLOR,
    ATTR_EFFECT,
)
from homeassistant.util import color

from . import (
    DOMAIN,
    CONF_MODEL,
    XIAOMI_CONFIG_SCHEMA as PLATFORM_SCHEMA,  # noqa: F401
    MiotToggleEntity,
    ToggleSubEntity,
    async_setup_config_entry,
    bind_services_to_entries,
)
from .core.miot_spec import (
    MiotSpec,
    MiotService,
)
from miio.utils import (
    rgb_to_int,
    int_to_rgb,
)

try:
    # hass 2021.4.0b0+
    from homeassistant.components.light import (
        COLOR_MODE_ONOFF,
        COLOR_MODE_BRIGHTNESS,
        COLOR_MODE_COLOR_TEMP,
        COLOR_MODE_HS,
    )
except ImportError:
    COLOR_MODE_ONOFF = 'onoff'
    COLOR_MODE_BRIGHTNESS = 'brightness'
    COLOR_MODE_COLOR_TEMP = 'color_temp'
    COLOR_MODE_HS = 'hs'

_LOGGER = logging.getLogger(__name__)
DATA_KEY = f'{ENTITY_DOMAIN}.{DOMAIN}'

SERVICE_TO_METHOD = {}


async def async_setup_entry(hass, config_entry, async_add_entities):
    await async_setup_config_entry(hass, config_entry, async_setup_platform, async_add_entities, ENTITY_DOMAIN)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    hass.data.setdefault(DATA_KEY, {})
    hass.data[DOMAIN]['add_entities'][ENTITY_DOMAIN] = async_add_entities
    model = str(config.get(CONF_MODEL) or '')
    entities = []
    if model.find('mrbond.airer') >= 0:
        pass
    else:
        miot = config.get('miot_type')
        if miot:
            spec = await MiotSpec.async_from_type(hass, miot)
            for srv in spec.get_services(ENTITY_DOMAIN):
                if not srv.get_property('on'):
                    continue
                entities.append(MiotLightEntity(config, srv))
    for entity in entities:
        hass.data[DOMAIN]['entities'][entity.unique_id] = entity
    async_add_entities(entities, update_before_add=True)
    bind_services_to_entries(hass, SERVICE_TO_METHOD)


class MiotLightEntity(MiotToggleEntity, LightEntity):
    def __init__(self, config: dict, miot_service: MiotService, **kwargs):
        kwargs.setdefault('logger', _LOGGER)
        super().__init__(miot_service, config=config, **kwargs)

        self._prop_power = miot_service.get_property('on')
        self._prop_mode = miot_service.get_property('mode')
        self._prop_brightness = miot_service.get_property('brightness')
        self._prop_color_temp = miot_service.get_property('color_temperature')
        self._prop_color = miot_service.get_property('color')

        self._srv_ambient_custom = miot_service.spec.get_service('ambient_light_custom')
        if self._srv_ambient_custom:
            if not self._prop_color:
                self._prop_color = self._srv_ambient_custom.get_property('color')

        self._attr_supported_color_modes = set()
        if self._prop_power:
            self._attr_supported_color_modes.add(COLOR_MODE_ONOFF)
        if self._prop_brightness:
            self._supported_features |= SUPPORT_BRIGHTNESS
            self._attr_supported_color_modes.add(COLOR_MODE_BRIGHTNESS)
        if self._prop_color_temp:
            self._supported_features |= SUPPORT_COLOR_TEMP
            self._attr_supported_color_modes.add(COLOR_MODE_COLOR_TEMP)
        if self._prop_color:
            self._supported_features |= SUPPORT_COLOR
            self._attr_supported_color_modes.add(COLOR_MODE_HS)
        if self._prop_mode:
            self._supported_features |= SUPPORT_EFFECT

    def turn_on(self, **kwargs):
        ret = False
        if not self.is_on:
            ret = self.set_property(self._prop_power, True)

        if self._prop_brightness and ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            per = brightness / 255
            val = per * 100
            if self._prop_brightness.value_range:
                val = per * self._prop_brightness.range_max()
            _LOGGER.debug('Setting light: %s brightness: %s %s%%', self.name, brightness, per * 100)
            ret = self.set_property(self._prop_brightness, round(val))

        if self._prop_color_temp and ATTR_COLOR_TEMP in kwargs:
            mired = kwargs[ATTR_COLOR_TEMP]
            color_temp = self.translate_mired(mired)
            _LOGGER.debug('Setting light: %s color temperature: %s mireds, %s ct', self.name, mired, color_temp)
            ret = self.set_property(self._prop_color_temp, color_temp)

        if self._prop_color and ATTR_HS_COLOR in kwargs:
            rgb = color.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            num = rgb_to_int(rgb)
            _LOGGER.debug('Setting light: %s color: %s', self.name, rgb)
            ret = self.set_property(self._prop_color, num)

        if self._prop_mode and ATTR_EFFECT in kwargs:
            val = self._prop_mode.list_value(kwargs[ATTR_EFFECT])
            _LOGGER.debug('Setting light: %s effect: %s(%s)', self.name, kwargs[ATTR_EFFECT], val)
            ret = self.set_property(self._prop_mode, val)

        return ret

    @property
    def brightness(self):
        """Return the brightness of this light between 0..255."""
        val = None
        if self._prop_brightness:
            val = self._prop_brightness.from_dict(self._state_attrs)
        if val is None:
            return None
        rmx = 100
        if self._prop_brightness.value_range:
            rmx = self._prop_brightness.range_max()
        return round(255 / rmx * int(val))

    @property
    def hs_color(self):
        """Return the hue and saturation color value [float, float]."""
        rgb = self.rgb_color
        if rgb is not None:
            return color.color_RGB_to_hs(*rgb)
        return None

    @property
    def rgb_color(self):
        """Return the rgb color value [int, int, int]."""
        if self._prop_color:
            num = round(self._prop_color.from_dict(self._state_attrs) or 0)
            return int_to_rgb(num)
        return None

    @property
    def color_temp(self):
        if not self._prop_color_temp:
            return None
        return self.translate_mired(self._prop_color_temp.from_dict(self._state_attrs) or 2700)

    @property
    def min_mireds(self):
        if not self._prop_color_temp:
            return None
        return self.translate_mired(self._prop_color_temp.value_range[1] or 5700)

    @property
    def max_mireds(self):
        if not self._prop_color_temp:
            return None
        return self.translate_mired(self._prop_color_temp.value_range[0] or 2700)

    @staticmethod
    def translate_mired(num):
        try:
            return round(1000000 / num)
        except TypeError:
            return round(1000000 / 2700)

    @property
    def effect_list(self):
        if self._prop_mode:
            return self._prop_mode.list_descriptions()
        return None

    @property
    def effect(self):
        if self._prop_mode:
            val = self._prop_mode.from_dict(self._state_attrs)
            if val is not None:
                return self._prop_mode.list_description(val)
        return None


class MiotLightSubEntity(MiotLightEntity, ToggleSubEntity):
    def __init__(self, parent, miot_service: MiotService):
        prop_power = miot_service.get_property('on')
        ToggleSubEntity.__init__(self, parent, prop_power.full_name, {
            'keys': list((miot_service.mapping() or {}).keys()),
        })
        MiotLightEntity.__init__(self, {
            **parent.miot_config,
            'name': f'{parent.device_name}',
        }, miot_service, device=parent.miot_device)
        self.entity_id = miot_service.generate_entity_id(self)
        self._prop_power = prop_power

    def update(self, data=None):
        super().update(data)
        if not self._available:
            return

    async def async_update(self):
        await self.hass.async_add_executor_job(partial(self.update))


class LightSubEntity(ToggleSubEntity, LightEntity):
    _brightness = None
    _color_temp = None

    def update(self, data=None):
        super().update(data)
        if self._available:
            attrs = self._state_attrs
            self._brightness = attrs.get('brightness', 0)
            self._color_temp = attrs.get('color_temp', 0)

    def turn_on(self, **kwargs):
        self.call_parent(['turn_on_light', 'turn_on'], **kwargs)

    def turn_off(self, **kwargs):
        self.call_parent(['turn_off_light', 'turn_off'], **kwargs)

    @property
    def brightness(self):
        return self._brightness

    @property
    def color_temp(self):
        return self._color_temp

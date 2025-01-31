"""Support remote entity for Xiaomi Miot."""
import logging
import time
from functools import partial

from homeassistant.const import *  # noqa: F401
from homeassistant.components import remote
from homeassistant.components.remote import (
    DOMAIN as ENTITY_DOMAIN,
    RemoteEntity,
)

from miio.chuangmi_ir import (
    ChuangmiIr,
    DeviceException,
)

from . import (
    DOMAIN,
    CONF_MODEL,
    XIAOMI_CONFIG_SCHEMA as PLATFORM_SCHEMA,  # noqa: F401
    MiotEntity,
    async_setup_config_entry,
    bind_services_to_entries,
)
from .core.miot_spec import (
    MiotSpec,
)
from .core.xiaomi_cloud import (
    MiotCloud,
    MiCloudException,
)

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
    miot = config.get('miot_type')
    if miot:
        spec = await MiotSpec.async_from_type(hass, miot)
        if spec.name in ['remote_control', 'ir_remote_control']:
            if 'chuangmi.remote.' in model or 'chuangmi.ir.' in model:
                entities.append(MiotRemoteEntity(config, spec))
        elif model in [
            'xiaomi.wifispeaker.l05c',
            'xiaomi.wifispeaker.lx5a',
            'xiaomi.wifispeaker.lx06',
        ]:
            entities.append(MiotRemoteEntity(config, spec))
    for entity in entities:
        hass.data[DOMAIN]['entities'][entity.unique_id] = entity
    async_add_entities(entities, update_before_add=True)
    bind_services_to_entries(hass, SERVICE_TO_METHOD)


class MiotRemoteEntity(MiotEntity, RemoteEntity):
    def __init__(self, config, miot_spec: MiotSpec):
        self._miot_spec = miot_spec
        super().__init__(miot_service=None, config=config, logger=_LOGGER)
        host = config.get(CONF_HOST)
        token = config.get(CONF_TOKEN)
        self._device = ChuangmiIr(host, token)
        self._attr_should_poll = False

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        did = self.miot_did
        mic = self.miot_cloud
        irs = []
        if did and isinstance(mic, MiotCloud):
            dls = await mic.async_get_devices() or []
            for d in dls:
                if did != d.get('parent_id'):
                    continue
                ird = d.get('did')
                rdt = await self.hass.async_add_executor_job(
                    partial(mic.request_miot_api, 'v2/irdevice/controller/keys', {'did': ird})
                ) or {}
                kys = (rdt.get('result') or {}).get('keys', {})
                if not kys:
                    self.logger.info('%s: IR device %s(%s) have no keys: %s', self.name, ird, d.get('name'), rdt)
                irs.append({
                    'did': ird,
                    'name': d.get('name'),
                    'keys': kys,
                })
        if irs:
            self._state_attrs['ir_devices'] = irs

    def is_on(self):
        return True

    def send_remote_command(self, command, **kwargs):
        """Send commands to a device."""
        repeat = kwargs.get(remote.ATTR_NUM_REPEATS, remote.DEFAULT_NUM_REPEATS)
        delays = kwargs.get(remote.ATTR_DELAY_SECS, remote.DEFAULT_DELAY_SECS)
        did = kwargs.get(remote.ATTR_DEVICE)
        for _ in range(repeat):
            for cmd in command:
                try:
                    if f'{cmd}'[:4] == 'key:':
                        ret = self.send_cloud_command(did, cmd)
                    else:
                        ret = self._device.play(cmd)
                    self.logger.info('%s: Send IR command %s(%s) result: %s', self.name, cmd, kwargs, ret)
                except (DeviceException, MiCloudException) as exc:
                    self.logger.error('%s: Send IR command %s(%s) failed: %s', self.name, cmd, kwargs, exc)
                time.sleep(delays)

    def send_cloud_command(self, did, command):
        key = f'{command}'
        if key[:4] == 'key:':
            key = key[4:]
        try:
            key = int(key)
        except (TypeError, ValueError):
            key = None
        if not did or not key:
            self.logger.warning('%s: IR command %s to %s invalid for cloud.', self.name, command, did)
            return False
        mic = self.miot_cloud
        if not mic:
            return False
        res = mic.request_miot_api('v2/irdevice/controller/key/click', {
            'did': did,
            'key_id': key,
        }) or {}
        return res

    async def async_send_command(self, command, **kwargs):
        """Send commands to a device."""
        await self.hass.async_add_executor_job(
            partial(self.send_remote_command, command, **kwargs)
        )

    def learn_command(self, **kwargs):
        """Learn a command from a device."""
        raise NotImplementedError()

    def delete_command(self, **kwargs):
        """Delete commands from the database."""
        raise NotImplementedError()

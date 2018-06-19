"""Helper to help store data."""
import asyncio
import logging
import os
from typing import Dict, Optional

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import callback
from homeassistant.loader import bind_hass
from homeassistant.util import json
from homeassistant.helpers.event import async_call_later

STORAGE_DIR = '.storage'
_LOGGER = logging.getLogger(__name__)


@bind_hass
async def async_migrator(hass, old_path, store, *, old_conf_migrate_func=None):
    """Helper function to migrate old data to a store and then load data.

    async def old_conf_migrate_func(old_data)
    """
    def load_old_config():
        """Helper to load old config."""
        if not os.path.isfile(old_path):
            return None

        return json.load_json(old_path)

    config = await hass.async_add_executor_job(load_old_config)

    if config is None:
        return None

    if old_conf_migrate_func is not None:
        config = await old_conf_migrate_func(config)

    await store.async_save(config)
    await hass.async_add_executor_job(os.remove, old_path)
    return config


@bind_hass
class Store:
    """Class to help storing data."""

    def __init__(self, hass, version: int, key: str):
        """Initialize storage class."""
        self.version = version
        self.key = key
        self.hass = hass
        self._data = None
        self._unsub_delay_listener = None
        self._unsub_stop_listener = None
        self._write_lock = asyncio.Lock()

    @property
    def path(self):
        """Return the config path."""
        return self.hass.config.path(STORAGE_DIR, self.key)

    async def async_load(self):
        """Load data.

        If the expected version does not match the given version, the migrate
        function will be invoked with await migrate_func(version, config).
        """
        if self._data is not None:
            data = self._data
        else:
            data = await self.hass.async_add_executor_job(
                json.load_json, self.path, None)

            if data is None:
                return {}

        if data['version'] == self.version:
            return data['data']

        return await self._async_migrate_func(data['version'], data['data'])

    async def async_save(self, data: Dict, *, delay: Optional[int] = None):
        """Save data with an optional delay."""
        self._data = {
            'version': self.version,
            'key': self.key,
            'data': data,
        }

        self._async_cleanup_delay_listener()

        if delay is None:
            self._async_cleanup_stop_listener()
            await self._handle_write_data()
            return

        self._unsub_delay_listener = async_call_later(
            self.hass, delay, self._async_callback_delayed_write)

        self._async_ensure_stop_listener()

    @callback
    def _async_ensure_stop_listener(self):
        """Ensure that we write if we quit before delay has passed."""
        if self._unsub_stop_listener is None:
            self._unsub_stop_listener = self.hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, self._async_callback_stop_write)

    @callback
    def _async_cleanup_stop_listener(self):
        """Clean up a stop listener."""
        if self._unsub_stop_listener is not None:
            self._unsub_stop_listener()
            self._unsub_stop_listener = None

    @callback
    def _async_cleanup_delay_listener(self):
        """Clean up a delay listener."""
        if self._unsub_delay_listener is not None:
            self._unsub_delay_listener()
            self._unsub_delay_listener = None

    async def _async_callback_delayed_write(self, _now):
        """Handle a delayed write callback."""
        self._unsub_delay_listener = None
        self._async_cleanup_stop_listener()
        await self._handle_write_data()

    async def _async_callback_stop_write(self, _event):
        """Handle a write because Home Assistant is stopping."""
        self._unsub_stop_listener = None
        self._async_cleanup_delay_listener()
        await self._handle_write_data()

    async def _handle_write_data(self, *_args):
        """Handler to handle writing the config."""
        data = self._data
        self._data = None

        async with self._write_lock:
            try:
                await self.hass.async_add_executor_job(
                    self._write_data, self.path, data)
            except json.SerializationError as err:
                _LOGGER.error('Error writing config for %s: %s', self.key, err)
            except json.WriteError as err:
                _LOGGER.error('Error writing config for %s: %s', self.key, err)

    def _write_data(self, path: str, data: Dict):
        """Write the data."""
        if not os.path.isdir(os.path.dirname(path)):
            os.makedirs(os.path.dirname(path))

        _LOGGER.debug('Writing data for %s', self.key)
        json.save_json(path, data)

    async def _async_migrate_func(self, old_version, old_data):
        """Migrate to the new version."""
        raise NotImplementedError

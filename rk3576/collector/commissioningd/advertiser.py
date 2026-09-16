"""BlueZ legacy advertiser (ble-discovery-legacy-profile-v1).

One connectable legacy ADV_IND on LE 1M owned by ego-commissioningd:
general-discoverable flags, the fixed 128-bit service UUID, short local name
EGO-<discriminator>. No service data, no IP, no MAC, no device ID.
Registration failure reports discovery unavailable and never downgrades to an
alternate profile.
"""

from __future__ import annotations

import logging

from crypto_glue import SERVICE_UUID, CommissioningError

LOGGER = logging.getLogger("commissioningd.advertiser")

ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"


class LegacyAdvertiser:
    def __init__(self, discriminator: str, adapter_path: str = "/org/bluez/hci0"):
        self._local_name = f"EGO-{discriminator}"
        self._adapter_path = adapter_path
        self._bus = None
        self._path = "/org/ego/commissioning/advertisement0"

    def register(self) -> None:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop

        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus.Interface(adapter, ADVERTISING_MANAGER_IFACE)
        advertisement = LegacyAdvertisement(
            self._bus, self._path, self._local_name
        )
        try:
            # unregister any stale registration owned by a previous instance
            manager.UnregisterAdvertisement(dbus.ObjectPath(self._path))
        except dbus.DBusException:
            pass
        try:
            manager.RegisterAdvertisement(
                dbus.ObjectPath(self._path),
                dbus.Dictionary({}, signature="sv"),
            )
        except dbus.DBusException as exc:
            LOGGER.error("legacy advertisement registration failed: %s", exc.get_dbus_name())
            raise CommissioningError("UNAVAILABLE") from exc
        LOGGER.info("legacy advertisement registered: name=%s", self._local_name)

    def unregister(self) -> None:
        if self._bus is None:
            return
        import dbus

        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus.Interface(adapter, ADVERTISING_MANAGER_IFACE)
        try:
            manager.UnregisterAdvertisement(dbus.ObjectPath(self._path))
        except dbus.DBusException:
            pass


class LegacyAdvertisement:
    """org.bluez.LEAdvertisement1 implementation (legacy ADV_IND profile)."""

    def __init__(self, bus, path: str, local_name: str):
        self._path = path
        self._props = {
            "Type": "peripheral",
            "LocalName": local_name,
            "ServiceUUIDs": [SERVICE_UUID],
            "Discoverable": True,
            "Includes": [],
        }
        bus.register_object(path, self, None)

    def Release(self):  # noqa: N802 - D-Bus method name
        LOGGER.warning("advertisement released by BlueZ")

    def GetAll(self, interface: str):  # noqa: N802
        if interface != ADVERTISEMENT_IFACE:
            raise KeyError(interface)
        return self._props

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
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"


class LegacyAdvertiser:
    def __init__(self, discriminator: str, adapter_path: str = "/org/bluez/hci0"):
        self._local_name = f"EGO-{discriminator}"
        self._adapter_path = adapter_path
        self._bus = None
        self._path = "/org/ego/commissioning/advertisement0"
        self._advertisement = None

    def prepare(self) -> None:
        from dbus.mainloop.glib import DBusGMainLoop

        DBusGMainLoop(set_as_default=True)
        if self._bus is None:
            import dbus

            self._bus = dbus.SystemBus()
            self._advertisement = _LegacyAdvertisement(self._bus, self._path, self._local_name)

    def register(self, wait_seconds: float = 30.0) -> None:
        from ble_gatt import _call_and_wait, dbus_dictionary, dbus_interface, dbus_object_path

        self.prepare()
        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus_interface(adapter, ADVERTISING_MANAGER_IFACE)
        try:
            manager.UnregisterAdvertisement(dbus_object_path(self._path))
        except Exception:  # noqa: BLE001 - not registered yet
            pass
        _call_and_wait(
            manager,
            "RegisterAdvertisement",
            (dbus_object_path(self._path), dbus_dictionary({})),
            wait_seconds=wait_seconds,
            description="legacy advertisement",
        )
        LOGGER.info("legacy advertisement registered: name=%s", self._local_name)

    def unregister(self) -> None:
        if self._bus is None:
            return
        import dbus

        from ble_gatt import dbus_interface, dbus_object_path

        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus_interface(adapter, ADVERTISING_MANAGER_IFACE)
        try:
            manager.UnregisterAdvertisement(dbus_object_path(self._path))
        except Exception:  # noqa: BLE001
            pass


class _LegacyAdvertisement:
    """org.bluez.LEAdvertisement1 implementation (legacy ADV_IND profile)."""

    def __init__(self, bus, path: str, local_name: str):
        import dbus
        import dbus.service

        parent = self

        class _Impl(dbus.service.Object):
            def __init__(self, bus, path):
                super().__init__(bus, path)

            @dbus.service.method(ADVERTISEMENT_IFACE, in_signature="", out_signature="")
            def Release(self):  # noqa: N802
                LOGGER.warning("advertisement released by BlueZ")

            @dbus.service.method(
                PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}"
            )
            def GetAll(self, interface):  # noqa: N802
                if interface != ADVERTISEMENT_IFACE:
                    raise dbus.exceptions.DBusException(
                        "no such interface",
                        name="org.freedesktop.DBus.Error.InvalidArgs",
                    )
                return {
                    "Type": dbus.String("peripheral"),
                    "LocalName": dbus.String(local_name),
                    "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s"),
                    "Discoverable": dbus.Boolean(True),
                    "Includes": dbus.Array([], signature="s"),
                    # A legacy ADV_IND carries at most 31 bytes. Flags (3) + the
                    # 128-bit service UUID (18) + "EGO-ABC" (9) already fill 30,
                    # so BlueZ's default TX-power field (3) overflows the packet
                    # and the controller rejects it with
                    # "Failed to add UUID: Authentication Failed (0x05)".
                    "IncludeTxPower": dbus.Boolean(False),
                }

        self._impl = _Impl(bus, path)

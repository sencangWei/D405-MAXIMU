"""NetworkManager system D-Bus credential adapter (ble-wifi-commissioning-v1).

Only this adapter ever sees decrypted Wi-Fi credentials. Credentials are
passed in memory through D-Bus method calls: never shell commands, never a
command line, never a temporary file, never a log line. A failed temporary
profile is removed before the error is reported.
"""

from __future__ import annotations

import logging
import time

from crypto_glue import CommissioningError, WINDOW_SECONDS

LOGGER = logging.getLogger("commissioningd.network")

NM_BUS = "org.freedesktop.NetworkManager"
NM_IFACE = "org.freedesktop.NetworkManager"
NM_SETTINGS_IFACE = "org.freedesktop.NetworkManager.Settings"
NM_CONNECTION_IFACE = "org.freedesktop.NetworkManager.Settings.Connection"
NM_DEVICE_IFACE = "org.freedesktop.NetworkManager.Device"
NM_WIFI_IFACE = "org.freedesktop.NetworkManager.Device.Wireless"
NM_ACTIVE_CONN_IFACE = "org.freedesktop.NetworkManager.Connection.Active"
NM_STATE_ACTIVATED = 2  # NM_ACTIVE_CONNECTION_STATE_ACTIVATED
NM_DEVICE_STATE_ACTIVATED = 100


class NetworkManagerBackend:
    """Approved NetworkManager adapter. Do not log ``credentials``."""

    def __init__(self, wifi_device: str = "wlan0", https_origin: str = "",
                 server_certificate_sha256: str = ""):
        self._wifi_device = wifi_device
        self._https_origin = https_origin
        self._server_certificate_sha256 = server_certificate_sha256
        self._connection_path: str | None = None
        self._active_path: str | None = None

    def apply_credentials(self, credentials: dict, deadline_seconds: float = WINDOW_SECONDS) -> tuple[str, str]:
        """Apply credentials; returns ("", "") — origin/cert come from config."""
        import dbus

        bus = dbus.SystemBus()
        device_path = self._find_wifi_device(bus)
        connection = self._build_connection(credentials)
        settings = bus.get_object(NM_BUS, "/org/freedesktop/NetworkManager/Settings")
        manager = bus.get_object(NM_BUS, "/org/freedesktop/NetworkManager")
        try:
            self._connection_path, self._active_path = settings.AddAndActivateConnection2(
                connection,
                device_path,
                dbus.ObjectPath("/"),
                dbus.Dictionary({}, signature="sv"),
                dbus_interface=NM_SETTINGS_IFACE,
            )[:2]
        except dbus.DBusException as exc:
            LOGGER.warning("NetworkManager rejected connection profile: %s", exc.get_dbus_name())
            raise CommissioningError("NETWORK_FAILED") from exc
        try:
            self._wait_activated(bus, deadline_seconds)
        except CommissioningError:
            self.cleanup_failed()
            raise
        return (self._https_origin, self._server_certificate_sha256)

    def cleanup_failed(self) -> None:
        import dbus

        bus = dbus.SystemBus()
        manager = bus.get_object(NM_BUS, "/org/freedesktop/NetworkManager")
        if self._active_path:
            try:
                manager.DeactivateConnection(
                    dbus.ObjectPath(self._active_path), dbus_interface=NM_IFACE
                )
            except dbus.DBusException:
                pass
        if self._connection_path:
            try:
                connection = bus.get_object(NM_BUS, self._connection_path)
                connection.Delete(dbus_interface=NM_CONNECTION_IFACE)
            except dbus.DBusException:
                pass
        self._active_path = None
        self._connection_path = None

    def _find_wifi_device(self, bus) -> "dbus.ObjectPath":
        import dbus

        manager = bus.get_object(NM_BUS, "/org/freedesktop/NetworkManager")
        devices = manager.GetDevices(dbus_interface=NM_IFACE)
        for path in devices:
            device = bus.get_object(NM_BUS, path)
            iface = device.Get(NM_DEVICE_IFACE, "Interface", dbus_interface="org.freedesktop.DBus.Properties")
            device_type = device.Get(NM_DEVICE_IFACE, "DeviceType", dbus_interface="org.freedesktop.DBus.Properties")
            if str(iface) == self._wifi_device and int(device_type) == 2:  # NM_DEVICE_TYPE_WIFI
                return path
        raise CommissioningError("UNAVAILABLE")

    def _build_connection(self, credentials: dict) -> dict:
        import dbus

        ssid = credentials["ssid"].encode()
        mode = credentials["security_mode"]
        wireless = {
            "ssid": dbus.ByteArray(ssid),
            "mode": "infrastructure",
        }
        connection: dict = {
            "connection": {
                "type": "802-11-wireless",
                "autoconnect": dbus.Boolean(False),
            },
            "802-11-wireless": wireless,
        }
        if mode == "OPEN":
            pass  # OPEN has no passphrase/security settings
        elif mode == "WPA2_PSK":
            connection["802-11-wireless-security"] = {
                "key-mgmt": "wpa-psk",
                "psk": credentials["passphrase"],
            }
        elif mode == "WPA3_SAE":
            connection["802-11-wireless-security"] = {
                "key-mgmt": "sae",
                "psk": credentials["passphrase"],
            }
        else:
            raise CommissioningError("INVALID")
        return connection

    def _wait_activated(self, bus, deadline_seconds: float) -> None:
        import dbus

        deadline = time.monotonic() + min(deadline_seconds, WINDOW_SECONDS)
        while time.monotonic() < deadline:
            try:
                active = bus.get_object(NM_BUS, self._active_path)
                state = int(
                    active.Get(
                        NM_ACTIVE_CONN_IFACE,
                        "State",
                        dbus_interface="org.freedesktop.DBus.Properties",
                    )
                )
            except dbus.DBusException as exc:
                raise CommissioningError("NETWORK_FAILED") from exc
            if state == NM_STATE_ACTIVATED:
                return
            if state == 4:  # NM_ACTIVE_CONNECTION_STATE_DEACTIVATED
                raise CommissioningError("NETWORK_FAILED")
            time.sleep(1.0)
        raise CommissioningError("TIMEOUT")

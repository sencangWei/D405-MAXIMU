"""BlueZ GATT application (ble-discovery-v1 + ble-wifi-commissioning-v1).

Characteristics (128-bit, base f3e0f8d0-7a11-4c9e-9d4b-45474f4f00XX):
  0002 DeviceIdentity  READ                 canonical identity JSON
  0003 BindingChallenge WRITE               16-32 fresh bytes (invalidates old)
  0004 BindingResponse  READ, NOTIFY        HMAC-SHA256 binding proof
  0005 Control          WRITE               framed BEGIN/CANCEL (message 5)
  0006 DeviceKey        READ                 X25519 device public key (raw value)
  0007 Credentials      WRITE               framed AES-GCM envelope (message 7)
  0008 NetworkStatus    READ, NOTIFY        network-ready status JSON

BlueZ discovers the object tree through
``org.freedesktop.DBus.ObjectManager.GetManagedObjects`` on the application
root path; without it ``RegisterApplication`` fails with
``gatt-database.c:client_ready_cb() No object received``.

Sanitized errors only: no challenge material, proofs, or session facts appear
in D-Bus error text beyond the stable error code name.
"""

from __future__ import annotations

import json
import logging

from crypto_glue import (
    CHAR_CHALLENGE,
    CHAR_CONTROL,
    CHAR_CREDENTIALS,
    CHAR_DEVICE_KEY,
    CHAR_IDENTITY,
    CHAR_RESPONSE,
    CHAR_STATUS,
    SERVICE_UUID,
    CommissioningError,
)

LOGGER = logging.getLogger("commissioningd.gatt")

GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHARACTERISTIC_IFACE = "org.bluez.GattCharacteristic1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

APP_PATH = "/org/ego/commissioning"
SERVICE_PATH = f"{APP_PATH}/service0"


def _error_name(code: str) -> str:
    return f"org.ego.Commissioning.Error.{code}"


class CommissioningGattApp:
    def __init__(self, state_machine, adapter_path: str = "/org/bluez/hci0"):
        self._sm = state_machine
        self._adapter_path = adapter_path
        self._bus = None
        self._managed: dict = {}
        self._objects: list = []
        self._chars: dict[str, object] = {}

    # ------------------------------------------------------------------
    def register(self) -> None:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop

        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SystemBus()
        self._register_objects()
        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus.Interface(adapter, GATT_MANAGER_IFACE)
        try:
            manager.UnregisterApplication(dbus.ObjectPath(APP_PATH))
        except dbus.DBusException:
            pass
        try:
            manager.RegisterApplication(
                dbus.ObjectPath(APP_PATH), dbus.Dictionary({}, signature="sv")
            )
        except dbus.DBusException as exc:
            LOGGER.error("GATT application registration failed: %s", exc.get_dbus_name())
            raise CommissioningError("UNAVAILABLE") from exc
        LOGGER.info("GATT application registered (%d objects)", len(self._managed))

    def unregister(self) -> None:
        if self._bus is None:
            return
        import dbus

        adapter = self._bus.get_object("org.bluez", self._adapter_path)
        manager = dbus.Interface(adapter, GATT_MANAGER_IFACE)
        try:
            manager.UnregisterApplication(dbus.ObjectPath(APP_PATH))
        except dbus.DBusException:
            pass

    def notify_status(self) -> None:
        char = self._chars.get(CHAR_STATUS)
        if char is not None:
            char.emit_value(self._status_value())

    def notify_response(self) -> None:
        char = self._chars.get(CHAR_RESPONSE)
        if char is not None:
            try:
                value = self._sm.read_response()
            except CommissioningError:
                return
            char.emit_value(value)

    # ------------------------------------------------------------------

    def _register_objects(self) -> None:
        import dbus
        import dbus.service

        characteristic_paths = []
        values = {
            "identity0": (CHAR_IDENTITY, ["read"], self._read_identity, None),
            "challenge0": (
                CHAR_CHALLENGE, ["write"], None,
                lambda value: (
                    self._sm.write_challenge(bytes(value)),
                    self.notify_response(),
                ),
            ),
            "response0": (CHAR_RESPONSE, ["read", "notify"], self._sm.read_response, None),
            "control0": (
                CHAR_CONTROL, ["write"], None,
                lambda value: (
                    self._sm.write_control(bytes(value)),
                    self.notify_status(),
                ),
            ),
            "devicekey0": (
                CHAR_DEVICE_KEY, ["read"],
                lambda: _b64u(self._sm.read_device_key()), None,
            ),
            "credentials0": (
                CHAR_CREDENTIALS, ["write"], None,
                lambda value: (
                    self._sm.write_credentials(bytes(value)),
                    self.notify_status(),
                ),
            ),
            "status0": (CHAR_STATUS, ["read", "notify"], self._status_value, None),
        }

        for suffix, (uuid, flags, read, write) in values.items():
            path = f"{APP_PATH}/{suffix}"
            characteristic_paths.append(dbus.ObjectPath(path))
            self._managed[path] = {
                GATT_CHARACTERISTIC_IFACE: {
                    "Service": dbus.ObjectPath(SERVICE_PATH),
                    "UUID": dbus.String(uuid),
                    "Flags": dbus.Array(flags, signature="s"),
                }
            }
            char = _GattCharacteristic(
                self._bus, path, uuid, flags, read=read, write=write
            )
            self._objects.append(char)
            self._chars[uuid] = char

        self._managed[SERVICE_PATH] = {
            GATT_SERVICE_IFACE: {
                "UUID": dbus.String(SERVICE_UUID),
                "Primary": dbus.Boolean(True),
                "Includes": dbus.Array([], signature="o"),
                "Characteristics": dbus.Array(characteristic_paths, signature="o"),
            }
        }
        root = _ObjectManager(self._bus, APP_PATH, self._managed)
        self._objects.append(root)

    def _read_identity(self) -> bytes:
        return self._sm.read_identity().encode()

    def _status_value(self) -> bytes:
        return json.dumps(self._sm.read_status(), separators=(",", ":")).encode()


class _ObjectManager:
    """Application root implementing org.freedesktop.DBus.ObjectManager."""

    def __init__(self, bus, path: str, managed: dict):
        import dbus.service

        class _Impl(dbus.service.Object):
            def __init__(self, bus, path):
                super().__init__(bus, path)

            @dbus.service.method(
                OBJECT_MANAGER_IFACE, in_signature="", out_signature="a{oa{sa{sv}}}"
            )
            def GetManagedObjects(self):  # noqa: N802
                return managed

        self._impl = _Impl(bus, path)


class _GattCharacteristic:
    """org.bluez.GattCharacteristic1 at its own object path."""

    def __init__(self, bus, path: str, uuid: str, flags: list, read=None, write=None):
        import dbus
        import dbus.service

        self.uuid = uuid
        self._notifying = False
        parent = self

        class _Impl(dbus.service.Object):
            def __init__(self, bus, path):
                super().__init__(bus, path)

            @dbus.service.method(
                PROPERTIES_IFACE, in_signature="s", out_signature="a{sv}"
            )
            def GetAll(self, interface):  # noqa: N802
                if interface != GATT_CHARACTERISTIC_IFACE:
                    raise dbus.exceptions.DBusException(
                        "no such interface",
                        name="org.freedesktop.DBus.Error.InvalidArgs",
                    )
                return {
                    "Service": dbus.ObjectPath(SERVICE_PATH),
                    "UUID": dbus.String(uuid),
                    "Flags": dbus.Array(flags, signature="s"),
                    "Notifying": dbus.Boolean(parent._notifying),
                }

            @dbus.service.method(PROPERTIES_IFACE, in_signature="ss", out_signature="v")
            def Get(self, interface, prop):  # noqa: N802
                return self.GetAll(interface)[prop]

            @dbus.service.method(
                GATT_CHARACTERISTIC_IFACE, in_signature="a{sv}", out_signature="ay"
            )
            def ReadValue(self, options):  # noqa: N802
                if read is None:
                    raise dbus.exceptions.DBusException(
                        "not readable", name="org.freedesktop.DBus.Error.NotSupported"
                    )
                try:
                    value = read()
                except CommissioningError as exc:
                    raise dbus.exceptions.DBusException(
                        exc.code, name=_error_name(exc.code)
                    ) from exc
                return dbus.Array(bytearray(value), signature="y")

            @dbus.service.method(
                GATT_CHARACTERISTIC_IFACE, in_signature="aya{sv}", out_signature=""
            )
            def WriteValue(self, value, options):  # noqa: N802
                if write is None:
                    raise dbus.exceptions.DBusException(
                        "not writable", name="org.freedesktop.DBus.Error.NotSupported"
                    )
                try:
                    write(bytes(bytearray(value)))
                except CommissioningError as exc:
                    raise dbus.exceptions.DBusException(
                        exc.code, name=_error_name(exc.code)
                    ) from exc

            @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature="", out_signature="")
            def StartNotify(self):  # noqa: N802
                if "notify" not in flags:
                    raise dbus.exceptions.DBusException(
                        "not notifiable", name="org.freedesktop.DBus.Error.NotSupported"
                    )
                parent._notifying = True

            @dbus.service.method(GATT_CHARACTERISTIC_IFACE, in_signature="", out_signature="")
            def StopNotify(self):  # noqa: N802
                parent._notifying = False

        self._impl = _Impl(bus, path)

    def emit_value(self, value: bytes) -> None:
        import dbus
        import dbus.lowlevel

        if not self._notifying:
            return
        message = dbus.lowlevel.SignalMessage(
            self._impl.object_path, PROPERTIES_IFACE, "PropertiesChanged"
        )
        message.append(
            dbus.String(GATT_CHARACTERISTIC_IFACE),
            dbus.Dictionary(
                {"Value": dbus.Array(bytearray(value), signature="y")}, signature="sv"
            ),
            dbus.Array([], signature="s"),
        )
        self._impl.connection.send_message(message)


def _b64u(value: str) -> bytes:
    from crypto_glue import b64u_decode

    return b64u_decode(value)

"""BlueZ GATT application (ble-discovery-v1 + ble-wifi-commissioning-v1).

Characteristics (128-bit, base f3e0f8d0-7a11-4c9e-9d4b-45474f4f00XX):
  0002 DeviceIdentity  READ                 canonical identity JSON
  0003 BindingChallenge WRITE               16-32 fresh bytes (invalidates old)
  0004 BindingResponse  READ, NOTIFY        HMAC-SHA256 binding proof
  0005 Control          WRITE               framed BEGIN/CANCEL (message 5)
  0006 DeviceKey        READ                 X25519 device public key (raw value)
  0007 Credentials      WRITE               framed AES-GCM envelope (message 7)
  0008 NetworkStatus    READ, NOTIFY        network-ready status JSON

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

APP_PATH = "/org/ego/commissioning"


def _error_name(code: str) -> str:
    return f"org.ego.Commissioning.Error.{code}"


class _GattObject:
    """Minimal D-Bus object: dispatches method calls, serves GetAll/Get."""

    def __init__(self, bus, path: str, interface: str, methods: dict, props: dict):
        self._methods = methods
        self._props = props
        self._interface = interface
        self._subscribers: set = set()
        bus.register_object(path, self._handle, None)

    def _handle(self, interface, method, path, args, **kwargs):
        import dbus

        if interface == PROPERTIES_IFACE:
            if method == "GetAll":
                return {self._interface: self._props}
            if method == "Get":
                name = args[1] if len(args) > 1 else args[0]
                if name in self._props:
                    return self._props[name]
                raise dbus.exceptions.DBusException(
                    "no such property", name="org.freedesktop.DBus.Error.InvalidArgs"
                )
            if method == "Set":
                raise dbus.exceptions.DBusException(
                    "read-only", name="org.freedesktop.DBus.Error.PropertyReadOnly"
                )
        handler = self._methods.get(method)
        if handler is None:
            raise dbus.exceptions.DBusException(
                f"unknown method {method}",
                name="org.freedesktop.DBus.Error.UnknownMethod",
            )
        return handler(*args)

    def emit_properties_changed(self, bus, path: str, value) -> None:
        import dbus

        signal = dbus.lowlevel.SignalMessage(
            path, PROPERTIES_IFACE, "PropertiesChanged"
        )
        signal.append(
            dbus.String(self._interface),
            dbus.Dictionary({"Value": dbus.Array(value, signature="y")}, signature="sv"),
            dbus.Array([], signature="s"),
            signature="sa{sv}as",
        )
        for name in self._subscribers:
            bus.send_message_with_reply(
                signal, dbus.ByteArray(name.encode()), reply_handler=lambda *_: None,
                error_handler=lambda *_: None,
                timeout=0,
            )


class CommissioningGattApp:
    def __init__(self, state_machine, adapter_path: str = "/org/bluez/hci0"):
        self._sm = state_machine
        self._adapter_path = adapter_path
        self._bus = None
        self._characteristics: dict[str, _GattObject] = {}
        self._service: _GattObject | None = None

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
                dbus.ObjectPath(APP_PATH),
                dbus.Dictionary({}, signature="sv"),
            )
        except dbus.DBusException as exc:
            LOGGER.error("GATT application registration failed: %s", exc.get_dbus_name())
            raise CommissioningError("UNAVAILABLE") from exc
        LOGGER.info("GATT application registered")

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
        char = self._characteristics.get(CHAR_STATUS)
        if char is not None:
            char.emit_properties_changed(
                self._bus, f"{APP_PATH}/status0", self._status_value()
            )

    def notify_response(self) -> None:
        char = self._characteristics.get(CHAR_RESPONSE)
        if char is not None:
            try:
                value = self._sm.read_response()
            except CommissioningError:
                return
            char.emit_properties_changed(
                self._bus, f"{APP_PATH}/response0", value
            )

    # ------------------------------------------------------------------

    def _register_objects(self) -> None:
        import dbus

        service_props = {
            "UUID": SERVICE_UUID,
            "Primary": True,
            "Includes": dbus.Array([], signature="o"),
        }
        self._service = _GattObject(
            self._bus, f"{APP_PATH}/service0", GATT_SERVICE_IFACE, {}, service_props
        )

        def characteristic(path_suffix: str, uuid: str, flags: list, methods: dict):
            props = {
                "Service": dbus.ObjectPath(f"{APP_PATH}/service0"),
                "UUID": uuid,
                "Flags": dbus.Array(flags, signature="s"),
            }
            obj = _GattObject(
                self._bus, f"{APP_PATH}/{path_suffix}", GATT_CHARACTERISTIC_IFACE,
                methods, props,
            )
            self._characteristics[uuid] = obj
            return obj

        def read_value(options):
            return dbus.Array(b"", signature="y")

        def notifiable(obj: _GattObject, value_fn):
            def start_notify():
                obj._subscribers.add("notify")

            def stop_notify():
                obj._subscribers.discard("notify")

            return start_notify, stop_notify

        def wrapped(handler):
            import dbus

            def inner(*args):
                try:
                    return handler(*args)
                except CommissioningError as exc:
                    raise dbus.exceptions.DBusException(
                        exc.code, name=_error_name(exc.code)
                    ) from exc

            return inner

        # ...0002 DeviceIdentity (READ)
        characteristic(
            "identity0", CHAR_IDENTITY, ["read"],
            {"ReadValue": wrapped(lambda options: dbus.Array(
                self._sm.read_identity().encode(), signature="y"))},
        )
        # ...0003 BindingChallenge (WRITE)
        def write_challenge(value, options):
            self._sm.write_challenge(bytes(bytearray(value)))
            self.notify_response()

        characteristic(
            "challenge0", CHAR_CHALLENGE, ["write"],
            {"WriteValue": wrapped(write_challenge)},
        )
        # ...0004 BindingResponse (READ, NOTIFY)
        response_char = characteristic(
            "response0", CHAR_RESPONSE, ["read", "notify"],
            {"ReadValue": wrapped(lambda options: dbus.Array(
                self._sm.read_response(), signature="y"))},
        )
        start, stop = notifiable(response_char, None)
        response_char._methods["StartNotify"] = wrapped(start)
        response_char._methods["StopNotify"] = wrapped(stop)
        # ...0005 Control (WRITE, framed BEGIN/CANCEL)
        characteristic(
            "control0", CHAR_CONTROL, ["write"],
            {"WriteValue": wrapped(lambda value, options: (
                self._sm.write_control(bytes(bytearray(value))),
                self.notify_status(),
            )[0])},
        )
        # ...0006 DeviceKey (READ, raw X25519 public key value, unwrapped)
        characteristic(
            "devicekey0", CHAR_DEVICE_KEY, ["read"],
            {"ReadValue": wrapped(lambda options: dbus.Array(
                bytes(_b64u(self._sm.read_device_key())), signature="y"))},
        )
        # ...0007 Credentials (WRITE, framed AES-GCM envelope)
        characteristic(
            "credentials0", CHAR_CREDENTIALS, ["write"],
            {"WriteValue": wrapped(lambda value, options: (
                self._sm.write_credentials(bytes(bytearray(value))),
                self.notify_status(),
            )[0])},
        )
        # ...0008 NetworkStatus (READ, NOTIFY)
        status_char = characteristic(
            "status0", CHAR_STATUS, ["read", "notify"],
            {"ReadValue": wrapped(lambda options: dbus.Array(
                self._status_value(), signature="y"))},
        )
        start, stop = notifiable(status_char, None)
        status_char._methods["StartNotify"] = wrapped(start)
        status_char._methods["StopNotify"] = wrapped(stop)

    def _status_value(self) -> bytes:
        return json.dumps(self._sm.read_status(), separators=(",", ":")).encode()


def _b64u(value: str) -> bytes:
    from crypto_glue import b64u_decode

    return b64u_decode(value)

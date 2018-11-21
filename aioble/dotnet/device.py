import asyncio
from aioble.device import Device

from aioble.dotnet.centralmanager import CentralManagerDotNet as CentralManager
from aioble.dotnet.utils import wrap_dotnet_task
from aioble.dotnet.service import ServiceDotNet as Service
from aioble.dotnet.characteristic import CharacteristicDotNet as Characteristic

from Windows.Devices.Bluetooth.Advertisement import BluetoothLEAdvertisementWatcher

from functools import wraps
from typing import Callable, Any

from UWPBluetoothPython import UWPBluetooth

from System import Array, Byte
from Windows.Devices.Bluetooth import BluetoothConnectionStatus
from Windows.Devices.Bluetooth.GenericAttributeProfile import (
    GattCharacteristic, GattCommunicationStatus
)

from Windows.Foundation import TypedEventHandler

class DeviceDotNet(Device):
    """The Device Base Class"""
    def __init__(self, address, loop=None):
        super(DeviceDotNet, self).__init__(loop)
        self.loop = loop if loop else asyncio.get_event_loop()
        self.address = address
        self.properties = None
        #UWP .NET 
        self._dotnet_task = None
        self._uwp_bluetooth = UWPBluetooth()
        self._devices = {}
        
    async def connect(self, timeout_sec=3):
        """Connect to device"""
        _evt = asyncio.Event()

        # THIS IS STILL NOT WORKING EVENT WISE
        def _advertisement_received(address, name):
            if address == hex(self.address):
                # Found Device
                _evt.set() # This event is never received by wait_for

        cm = CentralManager(self.loop)
        try:
            await cm.start_scan(_advertisement_received)
            await asyncio.wait_for(_evt.wait(), timeout_sec)
        except asyncio.TimeoutError:
            raise Exception("Device with address {0} was " "not found.".format(self.address))
        finally:
            await cm.stop_scan()

        # Initiate Connection
        self._dotnet_task = await wrap_dotnet_task(
            self._uwp_bluetooth.FromBluetoothAddressAsync(self.address),
            loop=self.loop,
        )

        def _ConnectionStatusChanged_Handler(sender, args):
            print("_ConnectionStatusChanged_Handler: " + args.ToString())

        self._dotnet_task.ConnectionStatusChanged += _ConnectionStatusChanged_Handler

        #Discover Services
        await self.discover_services()

    async def disconnect(self):
        """Disconnect to device"""
        self._dotnet_task.Dispose()
        self._dotnet_task = None

    async def is_connected(self):
        if self._dotnet_task:
            return self._dotnet_task.ConnectionStatus == BluetoothConnectionStatus.Connected

        else:
            return False

    async def get_properties(self):
        """Get Device Properties"""
        raise NotImplementedError()

    async def discover_services(self):
        """Discover Device Services"""
        if self.services:
            return self.services
        else:
            print("Get Services...")
            services = await wrap_dotnet_task(
                self._uwp_bluetooth.GetGattServicesAsync(self._dotnet_task), loop=self.loop
            )
            if services.Status == GattCommunicationStatus.Success:
                self.services = {s.Uuid.ToString(): Service(
                    device=s.Device,
                    uuid=s.Uuid.ToString(),
                    s_object=s) for s in services.Services}
            else:
                raise Exception("Could not get GATT services.")

            # Discover Characteristics for Each Service
            await asyncio.gather(
                *[
                    asyncio.ensure_future(service.discover_characteristics(), loop=self.loop)
                    for service_uuid, service in self.services.items()
                ]
            )
            self._services_resolved = True
            return self.services

    async def read_char(self):
        """Read Service Char"""
        raise NotImplementedError()

    async def write_char(self):
        """Write Service Char"""
        raise NotImplementedError()

    async def start_notify(self, uuid, callback: Callable[[str, Any], Any], **kwargs):
        """Start Notification Subscription"""
        # Find the Characteristic object
        for s_uuid, s in self.services.items():
            for c_uuid, c in s.characteristics.items():
                if str(uuid) == c_uuid:
                    characteristic = c.c_object

        if self._notification_callbacks.get(str(uuid)):
            await self.stop_notify(uuid)

        dotnet_callback = TypedEventHandler[GattCharacteristic, Array[Byte]](
            _notification_wrapper(callback)
        )
        status = await wrap_dotnet_task(
            self._uwp_bluetooth.StartNotify(characteristic, dotnet_callback), loop=self.loop
        )
        if status != GattCommunicationStatus.Success:
            raise Exception(
                "Could not start notify on {0}: {1}",
                characteristic.Uuid.ToString(),
                status,
            )

    async def stop_notify(self, uuid):
        """Stop Notification Subscription"""
        characteristic = self.characteristics.get(str(uuid))
        status = await wrap_dotnet_task(
            self._uwp_bluetooth.StopNotify(characteristic), loop=self.loop
        )
        if status != GattCommunicationStatus.Success:
            raise Exception(
                "Could not start notify on {0}: {1}",
                characteristic.Uuid.ToString(),
                status,
            )

def _notification_wrapper(func: Callable):
    @wraps(func)
    def dotnet_notification_parser(sender: Any, data: Any):
        return func(sender.Uuid.ToString(), bytearray(data))
    return dotnet_notification_parser
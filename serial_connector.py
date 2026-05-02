from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import serial.tools.list_ports_common as _lpc
from serial.tools.list_ports import comports

from base_logger import logger


FAN_CONTROLLER_VID = 0x2E8A
FAN_CONTROLLER_PID = 0x000A


@dataclass(frozen=True)
class FanControllerPort:
    device: str
    serial_number: str
    description: str
    vid: Optional[int]
    pid: Optional[int]
    product: Optional[str]
    manufacturer: Optional[str]
    location: Optional[str]
    interface: Optional[str]
    port_info: _lpc.ListPortInfo

    @property
    def identifier(self) -> str:
        return self.serial_number

    def to_dict(self) -> Dict[str, object]:
        return {
            "identifier": self.identifier,
            "serial_number": self.serial_number,
            "device": self.device,
            "description": self.description,
            "vid": self.vid,
            "pid": self.pid,
            "product": self.product,
            "manufacturer": self.manufacturer,
            "location": self.location,
            "interface": self.interface,
        }


class SerialFanControllerConnector:
    """Scans serial ports and identifies OpenFanController devices."""

    def __init__(self, vid: int = FAN_CONTROLLER_VID, pid: int = FAN_CONTROLLER_PID):
        self.vid = vid
        self.pid = pid

    def scan(self) -> List[FanControllerPort]:
        controllers: List[FanControllerPort] = []
        seen_serials: Set[str] = set()

        logger.debug(
            "Searching for fan controller serial ports with VID:0x%04X PID:0x%04X",
            self.vid,
            self.pid,
        )

        for port in comports():
            if not self._is_supported_port(port):
                continue

            serial_number = port.serial_number or ""
            if not serial_number:
                logger.warning(
                    "Skipping %s because fan controller serial_number is missing",
                    port.device,
                )
                continue

            if serial_number in seen_serials:
                continue

            seen_serials.add(serial_number)
            logger.debug(
                "Found fan controller on %s, SN: %s",
                port.device,
                serial_number,
            )
            controllers.append(
                FanControllerPort(
                    device=port.device,
                    serial_number=serial_number,
                    description=port.description or "",
                    vid=port.vid,
                    pid=port.pid,
                    product=port.product,
                    manufacturer=port.manufacturer,
                    location=port.location,
                    interface=port.interface,
                    port_info=port,
                )
            )

        return controllers

    def _is_supported_port(self, port: _lpc.ListPortInfo) -> bool:
        return port.vid == self.vid and port.pid == self.pid

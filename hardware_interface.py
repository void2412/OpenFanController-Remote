import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import serial as _serial

from base_logger import logger
from FanCommander import FanCommander
from serial_connector import FanControllerPort, SerialFanControllerConnector


@dataclass
class ConnectedFanController:
    port: FanControllerPort
    commander: FanCommander
    connected_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def identifier(self) -> str:
        return self.port.identifier

    def close(self) -> None:
        try:
            self.commander.close()
        except Exception as exc:
            logger.warning("Failed closing controller %s: %s", self.identifier, exc)

    def to_dict(self) -> Dict[str, object]:
        data = self.port.to_dict()
        data.update(
            {
                "connected": self.commander.is_open(),
                "connected_at": self.connected_at,
                "last_seen": self.last_seen,
            }
        )
        return data


class FanControllerHardwareInterface:
    """Auto-detects and maintains serial connections to fan controllers."""

    def __init__(self, poll_interval: float = 2.0):
        self.poll_interval = poll_interval
        self.connector = SerialFanControllerConnector()
        self._controllers: Dict[str, ConnectedFanController] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="fan-controller-detector",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.poll_interval + 1)

        with self._lock:
            controllers = list(self._controllers.values())
            self._controllers.clear()

        for controller in controllers:
            controller.close()

    def poll_once(self) -> List[ConnectedFanController]:
        ports = self.connector.scan()
        discovered = {port.identifier: port for port in ports}

        with self._lock:
            for identifier in list(self._controllers):
                if identifier not in discovered:
                    logger.info("Fan controller disconnected: %s", identifier)
                    self._controllers.pop(identifier).close()

            for identifier, port in discovered.items():
                existing = self._controllers.get(identifier)
                if existing:
                    existing.last_seen = time.time()
                    continue

                try:
                    commander = FanCommander(port.port_info)
                    self._controllers[identifier] = ConnectedFanController(
                        port=port,
                        commander=commander,
                    )
                    logger.info("Connected fan controller: %s", identifier)
                except _serial.SerialException as exc:
                    logger.warning(
                        "Unable to connect fan controller %s on %s: %s",
                        identifier,
                        port.device,
                        exc,
                    )

            return list(self._controllers.values())

    def list_controllers(self) -> List[Dict[str, object]]:
        with self._lock:
            return [controller.to_dict() for controller in self._controllers.values()]

    def get_controller(self, identifier: str) -> FanCommander:
        with self._lock:
            controller = self._controllers.get(identifier)
            if not controller:
                raise KeyError(f"Fan controller not connected: {identifier}")
            return controller.commander

    def get_all_fan_rpm(self, identifier: str) -> Dict[int, int]:
        return self.get_controller(identifier).get_all_fan_rpm()

    def set_fan_pwm(self, identifier: str, fan: int, pwm: float):
        return self.get_controller(identifier).set_fan_pwm(fan, pwm)

    def set_all_fan_pwm(self, identifier: str, pwm: float):
        return self.get_controller(identifier).set_all_fan_pwm(pwm)

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                logger.exception("Fan controller detection poll failed: %s", exc)
            self._stop_event.wait(self.poll_interval)

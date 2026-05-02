import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from base_logger import logger
from hardware_interface import FanControllerHardwareInterface


CONFIG_PATH = Path(__file__).with_name("fan_control_config.json")
_DEFAULT_CONFIG_PATH = object()
DEFAULT_CONFIG_DATA = {
    "sources": [],
    "controller_names": {},
    "power_switches": [],
    "curves": [],
}
DEFAULT_CURVE_POINTS = [
    {"temp": 30, "pwm": 25},
    {"temp": 50, "pwm": 45},
    {"temp": 70, "pwm": 75},
    {"temp": 85, "pwm": 100},
]
CURVE_TYPES = {"linear", "flat", "step", "trigger"}
DEFAULT_HYSTERESIS = 2.0
DEFAULT_RESPONSE_TIME = 1.0


@dataclass
class SensorSource:
    name: str
    url: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "url": self.url}


@dataclass
class PowerSwitchConfig:
    id: str
    name: str
    url: str
    sensor_groups: List[str] = field(default_factory=list)
    state: str = "unknown"
    desired_state: str = "off"
    last_command: str = "none"
    last_command_at: Optional[float] = None
    last_state_read_at: Optional[float] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "on_url": self.action_url("on"),
            "off_url": self.action_url("off"),
            "state_url": self.action_url("state"),
            "sensor_groups": self.sensor_groups,
            "state": self.state,
            "desired_state": self.desired_state,
            "last_command": self.last_command,
            "last_command_at": self.last_command_at,
            "last_state_read_at": self.last_state_read_at,
            "last_error": self.last_error,
        }

    def action_url(self, action: str) -> str:
        return f"{self.url.rstrip('/')}/{action}"


@dataclass
class CurveTarget:
    controller_id: str
    fan: str = "all"

    def to_dict(self) -> Dict[str, str]:
        return {"controller_id": self.controller_id, "fan": str(self.fan)}


@dataclass
class CurveConfig:
    id: str
    name: str
    sensor_ids: List[str]
    targets: List[CurveTarget]
    curve_type: str = "linear"
    points: List[Dict[str, float]] = field(default_factory=lambda: list(DEFAULT_CURVE_POINTS))
    hysteresis: float = DEFAULT_HYSTERESIS
    response_time: float = DEFAULT_RESPONSE_TIME
    enabled: bool = True
    last_value: Optional[float] = None
    last_pwm: Optional[float] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "sensor_ids": self.sensor_ids,
            "targets": [target.to_dict() for target in self.targets],
            "curve_type": self.curve_type,
            "points": self.points,
            "hysteresis": self.hysteresis,
            "response_time": self.response_time,
            "enabled": self.enabled,
            "last_value": self.last_value,
            "last_pwm": self.last_pwm,
            "last_error": self.last_error,
        }


def default_config_path() -> Path:
    return Path(os.getenv("CONFIG_PATH") or os.getenv("OFC_CONFIG_PATH") or CONFIG_PATH)


class FanControlConfigStore:
    def __init__(self, path: object = _DEFAULT_CONFIG_PATH):
        if path is _DEFAULT_CONFIG_PATH:
            self.path: Optional[Path] = default_config_path()
        elif path is None:
            self.path = None
        else:
            self.path = Path(path)
        self._lock = threading.RLock()
        self.sources: Dict[str, SensorSource] = {}
        self.power_switches: Dict[str, PowerSwitchConfig] = {}
        self.curves: Dict[str, CurveConfig] = {}
        self.controller_names: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        if self.path is None:
            return
        if not self.path.exists():
            self.save()
            return

        with self._lock:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.sources = {
                item["name"]: SensorSource(name=item["name"], url=item["url"])
                for item in data.get("sources", [])
            }
            self.controller_names = {
                str(identifier): str(name)
                for identifier, name in data.get("controller_names", {}).items()
                if str(name).strip()
            }
            self.power_switches = {}
            for item in data.get("power_switches", []):
                switch_id = self._clean_name(str(item.get("id") or item.get("name") or "switch"))
                switch_url = str(item.get("url") or "")
                if not switch_url:
                    switch_url = self._base_switch_url_from_legacy_urls(
                        str(item.get("on_url") or item.get("off_url") or item.get("state_url") or "")
                    )
                self.power_switches[switch_id] = PowerSwitchConfig(
                    id=switch_id,
                    name=str(item.get("name", switch_id)),
                    url=switch_url,
                    sensor_groups=[str(group) for group in item.get("sensor_groups", [])],
                    state=str(item.get("state", item.get("last_state", "unknown"))),
                    desired_state=str(item.get("desired_state", "off")),
                    last_command=str(item.get("last_command", "none")),
                    last_command_at=item.get("last_command_at"),
                    last_state_read_at=item.get("last_state_read_at"),
                    last_error=item.get("last_error"),
                )
            self.curves = {}
            for item in data.get("curves", []):
                targets = [
                    CurveTarget(
                        controller_id=target["controller_id"],
                        fan=str(target.get("fan", "all")),
                    )
                    for target in item.get("targets", [])
                ]
                self.curves[item["id"]] = CurveConfig(
                    id=item["id"],
                    name=item.get("name", item["id"]),
                    sensor_ids=list(item.get("sensor_ids", [])),
                    targets=targets,
                    curve_type=str(item.get("curve_type", "linear")),
                    points=list(item.get("points", DEFAULT_CURVE_POINTS)),
                    hysteresis=float(item.get("hysteresis", DEFAULT_HYSTERESIS)),
                    response_time=float(item.get("response_time", DEFAULT_RESPONSE_TIME)),
                    enabled=bool(item.get("enabled", True)),
                )

    def save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = DEFAULT_CONFIG_DATA | {
                "sources": [source.to_dict() for source in self.sources.values()],
                "controller_names": self.controller_names,
                "power_switches": [switch.to_dict() for switch in self.power_switches.values()],
                "curves": [curve.to_dict() for curve in self.curves.values()],
            }
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add_source(self, name: str, url: str) -> SensorSource:
        name = self._clean_name(name)
        if not name:
            raise ValueError("Source name is required")
        if not url.startswith(("http://", "https://")):
            raise ValueError("Sensor URL must start with http:// or https://")

        source = SensorSource(name=name, url=url)
        with self._lock:
            self.sources[name] = source
            self.save()
        return source

    def remove_source(self, name: str) -> None:
        with self._lock:
            self.sources.pop(name, None)
            for switch in self.power_switches.values():
                switch.sensor_groups = [group for group in switch.sensor_groups if group != name]
            self.save()

    def upsert_power_switch(self, data: Dict[str, object]) -> PowerSwitchConfig:
        switch_name = str(data.get("name") or data.get("id") or f"switch-{int(time.time())}")
        switch_id = self._clean_name(str(data.get("id") or switch_name))
        with self._lock:
            for existing_id, existing_switch in self.power_switches.items():
                if existing_switch.name.lower() == switch_name.lower():
                    switch_id = existing_id
                    break

        switch_url = str(data.get("url") or "").strip()
        if not switch_url:
            switch_url = self._base_switch_url_from_legacy_urls(
                str(data.get("on_url") or data.get("off_url") or data.get("state_url") or "")
            )
        if not switch_name.strip():
            raise ValueError("Switch name is required")
        if not switch_url.startswith(("http://", "https://")):
            raise ValueError("Switch URL must start with http:// or https://")

        sensor_groups = [str(group) for group in data.get("sensor_groups", [])]
        self._validate_switch_sensor_groups_available(switch_id, sensor_groups)
        switch = PowerSwitchConfig(
            id=switch_id,
            name=switch_name,
            url=switch_url.rstrip("/"),
            sensor_groups=sensor_groups,
        )
        with self._lock:
            existing = self.power_switches.get(switch_id)
            if existing:
                switch.state = existing.state
                switch.desired_state = existing.desired_state
                switch.last_command = existing.last_command
                switch.last_command_at = existing.last_command_at
                switch.last_state_read_at = existing.last_state_read_at
                switch.last_error = existing.last_error
            self.power_switches[switch_id] = switch
            self.save()
        return switch

    def remove_power_switch(self, switch_id: str) -> None:
        with self._lock:
            self.power_switches.pop(switch_id, None)
            self.save()

    def set_power_switch_status(
        self,
        switch_id: str,
        state: str,
        desired_state: str,
        last_command: Optional[str] = None,
        last_command_at: Optional[float] = None,
        last_state_read_at: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            switch = self.power_switches.get(switch_id)
            if not switch:
                return
            switch.state = state
            switch.desired_state = desired_state
            if last_command is not None:
                switch.last_command = last_command
            if last_command_at is not None:
                switch.last_command_at = last_command_at
            if last_state_read_at is not None:
                switch.last_state_read_at = last_state_read_at
            switch.last_error = error
            self.save()

    def snapshot_power_switches(self) -> List[PowerSwitchConfig]:
        with self._lock:
            return list(self.power_switches.values())

    def _validate_switch_sensor_groups_available(self, switch_id: str, sensor_groups: List[str]) -> None:
        seen = set()
        for group in sensor_groups:
            if group in seen:
                raise ValueError(f"Sensor group {group} is duplicated on this switch")
            seen.add(group)
            if group not in self.sources:
                raise ValueError(f"Sensor group {group} does not exist")

        for existing_id, existing_switch in self.power_switches.items():
            if existing_id == switch_id:
                continue
            for group in sensor_groups:
                if group in existing_switch.sensor_groups:
                    raise ValueError(
                        f"Sensor group {group} is already assigned to switch {existing_switch.name}"
                    )

    def upsert_curve(self, data: Dict[str, object]) -> CurveConfig:
        curve_name = str(data.get("name") or data.get("id") or f"curve-{int(time.time())}")
        curve_id = self._clean_name(str(data.get("id") or curve_name))
        with self._lock:
            for existing_id, existing_curve in self.curves.items():
                if existing_curve.name.lower() == curve_name.lower():
                    curve_id = existing_id
                    break

        curve_type = str(data.get("curve_type") or "linear").lower()
        if curve_type not in CURVE_TYPES:
            raise ValueError(f"Unsupported curve type: {curve_type}")

        targets = [
            CurveTarget(
                controller_id=str(target["controller_id"]),
                fan=str(target.get("fan", "all")),
            )
            for target in data.get("targets", [])
        ]
        self._validate_curve_targets_available(curve_id, targets)
        points = self._clean_points(data.get("points") or DEFAULT_CURVE_POINTS)
        curve = CurveConfig(
            id=curve_id,
            name=curve_name,
            sensor_ids=[str(sensor_id) for sensor_id in data.get("sensor_ids", [])],
            targets=targets,
            curve_type=curve_type,
            points=points,
            hysteresis=max(0.0, float(data.get("hysteresis", DEFAULT_HYSTERESIS))),
            response_time=max(0.0, float(data.get("response_time", DEFAULT_RESPONSE_TIME))),
            enabled=bool(data.get("enabled", True)),
        )
        with self._lock:
            self.curves[curve_id] = curve
            self.save()
        return curve

    def _validate_curve_targets_available(self, curve_id: str, targets: List[CurveTarget]) -> None:
        for existing_id, existing_curve in self.curves.items():
            if existing_id == curve_id:
                continue
            for target in targets:
                for existing_target in existing_curve.targets:
                    if target.controller_id != existing_target.controller_id:
                        continue
                    if self._fan_targets_overlap(target.fan, existing_target.fan):
                        raise ValueError(
                            "Fan {} on controller {} is already assigned to curve {}".format(
                                target.fan,
                                target.controller_id,
                                existing_curve.name,
                            )
                        )

    def _fan_targets_overlap(self, fan: str, existing_fan: str) -> bool:
        return fan == "all" or existing_fan == "all" or fan == existing_fan

    def remove_curve(self, curve_id: str) -> None:
        with self._lock:
            self.curves.pop(curve_id, None)
            self.save()

    def set_curve_enabled(self, curve_id: str, enabled: bool) -> CurveConfig:
        with self._lock:
            curve = self.curves[curve_id]
            curve.enabled = enabled
            self.save()
            return curve

    def set_controller_name(self, identifier: str, name: str) -> None:
        clean_name = name.strip()
        with self._lock:
            if clean_name:
                self.controller_names[identifier] = clean_name
            else:
                self.controller_names.pop(identifier, None)
            self.save()

    def get_controller_name(self, identifier: str) -> Optional[str]:
        with self._lock:
            return self.controller_names.get(identifier)

    def snapshot_sources(self) -> List[SensorSource]:
        with self._lock:
            return list(self.sources.values())

    def snapshot_curves(self) -> List[CurveConfig]:
        with self._lock:
            return list(self.curves.values())

    def _clean_points(self, points: Iterable[Dict[str, object]]) -> List[Dict[str, float]]:
        cleaned = []
        for point in points:
            temp = float(point["temp"])
            pwm = min(100.0, max(0.0, float(point["pwm"])))
            cleaned.append({"temp": temp, "pwm": pwm})
        return sorted(cleaned, key=lambda point: point["temp"])

    def _base_switch_url_from_legacy_urls(self, url: str) -> str:
        cleaned = url.strip().rstrip("/")
        for suffix in ("/on", "/off", "/state"):
            if cleaned.lower().endswith(suffix):
                return cleaned[: -len(suffix)]
        return cleaned

    def _clean_name(self, value: str) -> str:
        return "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip()).strip("-")


class RemoteSensorReader:
    def __init__(self, store: FanControlConfigStore):
        self.store = store
        self.last_errors: Dict[str, str] = {}
        self.last_ok_sources: Set[str] = set()

    def read_sensors(self) -> List[Dict[str, object]]:
        sensors: List[Dict[str, object]] = []
        errors: Dict[str, str] = {}
        ok_sources: Set[str] = set()

        for source in self.store.snapshot_sources():
            try:
                readings = self._fetch_temperature_readings(source.url)
                ok_sources.add(source.name)
                sensors.extend(self._extract_temperature_sensors(source.name, readings))
            except Exception as exc:
                errors[source.name] = str(exc)
                logger.warning("Unable to read sensor source %s: %s", source.name, exc)

        self.last_errors = errors
        self.last_ok_sources = ok_sources
        return sensors

    def _fetch_temperature_readings(self, url: str) -> List[Dict[str, object]]:
        candidates = [self._with_temperature_filter(url), url]
        last_error: Optional[Exception] = None

        for candidate in dict.fromkeys(candidates):
            try:
                request = urllib.request.Request(candidate, headers={"Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, list):
                    return payload
                raise ValueError("Sensor endpoint did not return a JSON array")
            except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc

        raise RuntimeError(last_error or "Unable to fetch sensor data")

    def _with_temperature_filter(self, url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        query.setdefault("type", "temperature")
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))

    def _extract_temperature_sensors(self, source_name: str, readings: List[Dict[str, object]]) -> List[Dict[str, object]]:
        sensors: List[Dict[str, object]] = []
        used_ids: Dict[str, int] = {}

        for reading in readings:
            name = str(reading.get("name") or reading.get("id") or "Temperature")
            unit = str(reading.get("unit", ""))
            sensor_type = str(reading.get("type", "")).lower()
            value = self._as_float(reading.get("value"))
            if value is None:
                continue
            if sensor_type != "temperature" and "c" not in unit.lower():
                continue

            sensor_key = self._clean_sensor_key(name)
            if not sensor_key:
                sensor_key = self._clean_sensor_key(str(reading.get("id") or "temperature"))
            sensor_id = f"{source_name}-{sensor_key or 'temperature'}"
            sensor_id = self._dedupe_sensor_id(sensor_id, used_ids, reading)

            sensors.append(
                {
                    "id": sensor_id,
                    "source": source_name,
                    "kind": "temperature",
                    "name": name,
                    "label": name,
                    "unit": "C",
                    "value": value,
                    "raw": reading,
                }
            )

        return sensors

    def _dedupe_sensor_id(
        self,
        sensor_id: str,
        used_ids: Dict[str, int],
        reading: Dict[str, object],
    ) -> str:
        if sensor_id not in used_ids:
            used_ids[sensor_id] = 1
            return sensor_id

        raw_suffix = self._clean_sensor_key(str(reading.get("id") or ""))
        suffix = raw_suffix or str(used_ids[sensor_id] + 1)
        candidate = f"{sensor_id}-{suffix}"
        while candidate in used_ids:
            used_ids[sensor_id] += 1
            candidate = f"{sensor_id}-{used_ids[sensor_id]}"
        used_ids[sensor_id] += 1
        used_ids[candidate] = 1
        return candidate

    def _clean_sensor_key(self, value: str) -> str:
        cleaned = []
        last_was_dash = False
        for ch in value.strip().lower():
            if ch.isalnum():
                cleaned.append(ch)
                last_was_dash = False
            elif not last_was_dash:
                cleaned.append("-")
                last_was_dash = True
        return "".join(cleaned).strip("-")

    def _as_float(self, value: object) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


class CurveEngine:
    def __init__(
        self,
        hardware: FanControllerHardwareInterface,
        store: FanControlConfigStore,
        sensors: RemoteSensorReader,
        interval: float = 2.0,
        power_switch_state_interval: float = 2.0,
    ):
        self.hardware = hardware
        self.store = store
        self.sensors = sensors
        self.interval = interval
        self.power_switch_state_interval = power_switch_state_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_sensors: List[Dict[str, object]] = []
        self._runtime: Dict[str, Dict[str, object]] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="fan-curve-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(self.interval, self.power_switch_state_interval) + 1)

    def evaluate_once(self, read_switch_state: bool = True) -> List[Dict[str, object]]:
        self.last_sensors = self.sensors.read_sensors()
        self.evaluate_power_switches(read_state=read_switch_state)
        sensor_by_id = {sensor["id"]: sensor for sensor in self.last_sensors}
        results = []

        for curve in self.store.snapshot_curves():
            if not curve.enabled:
                curve.last_error = None
                continue

            selected = [
                sensor_by_id[sensor_id]
                for sensor_id in curve.sensor_ids
                if sensor_id in sensor_by_id and sensor_by_id[sensor_id].get("value") is not None
            ]
            if not selected and curve.curve_type != "flat":
                curve.last_error = "No selected sensors are currently available"
                results.append(curve.to_dict())
                continue

            highest_value = max(float(sensor["value"]) for sensor in selected) if selected else None
            pwm, should_apply = self._resolve_pwm(curve, highest_value)
            curve.last_value = highest_value
            curve.last_pwm = pwm
            curve.last_error = None

            if not should_apply:
                results.append(curve.to_dict())
                continue

            for target in curve.targets:
                try:
                    if target.fan == "all":
                        self.hardware.set_all_fan_pwm(target.controller_id, pwm)
                    else:
                        self.hardware.set_fan_pwm(target.controller_id, int(target.fan), pwm)
                except Exception as exc:
                    curve.last_error = str(exc)
                    logger.warning("Unable to apply curve %s: %s", curve.id, exc)

            results.append(curve.to_dict())

        return results

    def evaluate_power_switches(
        self,
        read_state: bool = True,
        force_state_read: bool = False,
    ) -> List[Dict[str, object]]:
        ok_sources = set(getattr(self.sensors, "last_ok_sources", set()))
        results = []

        for switch in self.store.snapshot_power_switches():
            desired_state = self._desired_switch_state(switch, ok_sources)
            state = switch.state
            last_command = switch.last_command
            last_command_at = switch.last_command_at
            last_state_read_at = switch.last_state_read_at
            errors = []

            if desired_state != switch.last_command or self._has_confirmed_switch_mismatch(
                switch, desired_state
            ):
                try:
                    self._request_switch_url(switch.action_url(desired_state))
                    last_command = desired_state
                    last_command_at = time.time()
                except Exception as exc:
                    errors.append(f"{desired_state.upper()} command failed: {exc}")

            if read_state and self._should_read_switch_state(switch, force_state_read):
                try:
                    state = self._read_switch_state(switch.action_url("state"))
                    last_state_read_at = time.time()
                except Exception as exc:
                    last_state_read_at = time.time()
                    errors.append(f"State read failed: {exc}")

            self.store.set_power_switch_status(
                switch.id,
                state=state,
                desired_state=desired_state,
                last_command=last_command,
                last_command_at=last_command_at,
                last_state_read_at=last_state_read_at,
                error="; ".join(errors) if errors else None,
            )
            refreshed = self.store.power_switches.get(switch.id)
            results.append(refreshed.to_dict() if refreshed else switch.to_dict())

        return results

    def refresh_power_switch_states(self, force: bool = False) -> List[Dict[str, object]]:
        results = []
        for switch in self.store.snapshot_power_switches():
            if not self._should_read_switch_state(switch, force):
                results.append(switch.to_dict())
                continue

            state = switch.state
            last_state_read_at = switch.last_state_read_at
            error = None
            try:
                state = self._read_switch_state(switch.action_url("state"))
                last_state_read_at = time.time()
            except Exception as exc:
                last_state_read_at = time.time()
                error = f"State read failed: {exc}"

            self.store.set_power_switch_status(
                switch.id,
                state=state,
                desired_state=switch.desired_state,
                last_command=switch.last_command,
                last_command_at=switch.last_command_at,
                last_state_read_at=last_state_read_at,
                error=error,
            )
            refreshed = self.store.power_switches.get(switch.id)
            results.append(refreshed.to_dict() if refreshed else switch.to_dict())
        return results

    def _should_read_switch_state(self, switch: PowerSwitchConfig, force: bool = False) -> bool:
        if force or switch.last_state_read_at is None:
            return True
        return time.time() - float(switch.last_state_read_at) >= self.power_switch_state_interval

    def _has_confirmed_switch_mismatch(self, switch: PowerSwitchConfig, desired_state: str) -> bool:
        if switch.state not in {"on", "off"} or switch.state == desired_state:
            return False
        if switch.last_state_read_at is None:
            return True
        if switch.last_command_at is None:
            return True
        return float(switch.last_state_read_at) >= float(switch.last_command_at)

    def _desired_switch_state(self, switch: PowerSwitchConfig, ok_sources: Set[str]) -> str:
        if not switch.sensor_groups:
            return "off"
        return "on" if any(group in ok_sources for group in switch.sensor_groups) else "off"

    def _request_switch_url(self, url: str) -> None:
        request = urllib.request.Request(url, headers={"Accept": "application/json,text/plain,*/*"})
        with urllib.request.urlopen(request, timeout=5) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status < 200 or status >= 300:
                raise RuntimeError(f"HTTP {status}")

    def _read_switch_state(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"Accept": "application/json,text/plain,*/*"})
        with urllib.request.urlopen(request, timeout=5) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status < 200 or status >= 300:
                raise RuntimeError(f"HTTP {status}")
            text = response.read().decode("utf-8", errors="replace").strip()
        return self._parse_switch_state(text)

    def _parse_switch_state(self, text: str) -> str:
        if not text:
            return "unknown"
        try:
            payload = json.loads(text)
            parsed = self._state_from_json(payload)
            if parsed:
                return parsed
        except json.JSONDecodeError:
            pass

        normalized = text.strip().lower()
        if normalized in {"on", "1", "true", "enabled", "high"}:
            return "on"
        if normalized in {"off", "0", "false", "disabled", "low"}:
            return "off"
        return normalized[:80]

    def _state_from_json(self, payload: Any) -> Optional[str]:
        if isinstance(payload, bool):
            return "on" if payload else "off"
        if isinstance(payload, (int, float)):
            if payload == 1:
                return "on"
            if payload == 0:
                return "off"
        if isinstance(payload, str):
            return self._parse_switch_state(payload)
        if isinstance(payload, dict):
            for key in ("state", "status", "power", "relay", "on", "value"):
                if key in payload:
                    return self._state_from_json(payload[key])
        return None

    def _resolve_pwm(self, curve: CurveConfig, current_temp: Optional[float]) -> tuple[float, bool]:
        if curve.curve_type == "flat":
            return self.calculate_pwm(curve.curve_type, curve.points, 0.0), True

        if current_temp is None:
            return curve.last_pwm or 0.0, False

        desired_pwm = self.calculate_pwm(curve.curve_type, curve.points, current_temp)
        runtime = self._runtime.get(curve.id)
        signature = self._curve_runtime_signature(curve)
        now = time.time()
        if not runtime or runtime.get("signature") != signature:
            self._runtime[curve.id] = {
                "control_temp": current_temp,
                "pwm": desired_pwm,
                "updated_at": now,
                "signature": signature,
            }
            return desired_pwm, True

        hysteresis = max(0.0, curve.hysteresis)
        response_time = max(0.0, curve.response_time)
        previous_pwm = float(runtime["pwm"])
        control_temp = float(runtime["control_temp"])
        updated_at = float(runtime["updated_at"])
        if abs(current_temp - control_temp) < hysteresis:
            return previous_pwm, False
        if now - updated_at < response_time:
            return previous_pwm, False

        runtime["control_temp"] = current_temp
        runtime["pwm"] = desired_pwm
        runtime["updated_at"] = now
        return desired_pwm, True

    def _curve_runtime_signature(self, curve: CurveConfig) -> str:
        return json.dumps(
            {
                "type": curve.curve_type,
                "points": curve.points,
                "hysteresis": curve.hysteresis,
                "response_time": curve.response_time,
            },
            sort_keys=True,
        )

    def calculate_pwm(self, curve_type: str, points: List[Dict[str, float]], temp: float) -> float:
        curve_type = curve_type.lower()
        if curve_type == "flat":
            return self.flat_pwm(points)
        if curve_type == "step":
            return self.step_pwm(points, temp)
        if curve_type == "trigger":
            return self.trigger_pwm(points, temp)
        return self.interpolate_pwm(points, temp)

    def interpolate_pwm(self, points: List[Dict[str, float]], temp: float) -> float:
        if not points:
            return 0.0
        sorted_points = sorted(points, key=lambda point: point["temp"])
        if temp <= sorted_points[0]["temp"]:
            return sorted_points[0]["pwm"]
        if temp >= sorted_points[-1]["temp"]:
            return sorted_points[-1]["pwm"]

        for left, right in zip(sorted_points, sorted_points[1:]):
            if left["temp"] <= temp <= right["temp"]:
                span = right["temp"] - left["temp"]
                if span == 0:
                    return right["pwm"]
                ratio = (temp - left["temp"]) / span
                return round(left["pwm"] + ratio * (right["pwm"] - left["pwm"]), 2)

        return sorted_points[-1]["pwm"]

    def flat_pwm(self, points: List[Dict[str, float]]) -> float:
        if not points:
            return 0.0
        return sorted(points, key=lambda point: point["temp"])[0]["pwm"]

    def step_pwm(self, points: List[Dict[str, float]], temp: float) -> float:
        if not points:
            return 0.0
        sorted_points = sorted(points, key=lambda point: point["temp"])
        pwm = sorted_points[0]["pwm"]
        for point in sorted_points:
            if temp >= point["temp"]:
                pwm = point["pwm"]
            else:
                break
        return pwm

    def trigger_pwm(self, points: List[Dict[str, float]], temp: float) -> float:
        if not points:
            return 0.0
        sorted_points = sorted(points, key=lambda point: point["temp"])
        low_pwm = sorted_points[0]["pwm"]
        high_point = sorted_points[-1]
        return high_point["pwm"] if temp >= high_point["temp"] else low_pwm

    def _loop(self) -> None:
        next_curve_at = time.monotonic()
        next_state_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            try:
                if now >= next_curve_at:
                    read_switch_state = now >= next_state_at
                    self.evaluate_once(read_switch_state=read_switch_state)
                    next_curve_at = now + self.interval
                    if read_switch_state:
                        next_state_at = now + self.power_switch_state_interval
                elif now >= next_state_at:
                    self.refresh_power_switch_states()
                    next_state_at = now + self.power_switch_state_interval
            except Exception as exc:
                logger.exception("Curve engine evaluation failed: %s", exc)

            wait_until = min(next_curve_at, next_state_at)
            self._stop_event.wait(max(0.1, wait_until - time.monotonic()))

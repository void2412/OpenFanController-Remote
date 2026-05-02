import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fan_control_service import CurveEngine, FanControlConfigStore, RemoteSensorReader
from hardware_interface import FanControllerHardwareInterface
from serial_connector import SerialFanControllerConnector


class SerialConnectorTests(unittest.TestCase):
    def test_scan_deduplicates_by_serial_number(self):
        ports = [
            SimpleNamespace(
                device="COM3",
                serial_number="abc",
                description="OpenFanController",
                vid=0x2E8A,
                pid=0x000A,
                product="fan",
                manufacturer="void",
                location="1",
                interface=None,
            ),
            SimpleNamespace(
                device="COM4",
                serial_number="abc",
                description="OpenFanController",
                vid=0x2E8A,
                pid=0x000A,
                product="fan",
                manufacturer="void",
                location="2",
                interface=None,
            ),
            SimpleNamespace(device="COM5", serial_number="other", vid=1, pid=2),
        ]

        with patch("serial_connector.comports", return_value=ports):
            result = SerialFanControllerConnector().scan()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].identifier, "abc")
        self.assertEqual(result[0].device, "COM3")


class HardwareInterfaceTests(unittest.TestCase):
    def test_poll_once_connects_and_removes_by_serial_number(self):
        port = SimpleNamespace(identifier="abc", port_info=SimpleNamespace(), device="COM3")

        class FakeCommander:
            closed = False

            def __init__(self, port_info):
                self.port_info = port_info

            def is_open(self):
                return not self.closed

            def close(self):
                self.closed = True

        interface = FanControllerHardwareInterface()
        interface.connector.scan = lambda: [port]

        with patch("hardware_interface.FanCommander", FakeCommander):
            controllers = interface.poll_once()
            self.assertEqual([controller.identifier for controller in controllers], ["abc"])

            interface.connector.scan = lambda: []
            controllers = interface.poll_once()
            self.assertEqual(controllers, [])


class SensorReaderTests(unittest.TestCase):
    def test_extracts_all_temperature_sensor_names(self):
        store = FanControlConfigStore(path=None)
        reader = RemoteSensorReader(store)

        sensors = reader._extract_temperature_sensors(
            "voidpc",
            [
                {"type": "temperature", "name": "CPU Core 1", "unit": "C", "value": 54},
                {"type": "temperature", "name": "CPU Package", "unit": "C", "value": 62},
                {"type": "temperature", "name": "GPU Hot Spot", "unit": "C", "value": 71},
                {"type": "voltage", "name": "CPU Core VID", "unit": "V", "value": 1.2},
            ],
        )

        by_id = {sensor["id"]: sensor for sensor in sensors}
        self.assertEqual(len(sensors), 3)
        self.assertEqual(by_id["voidpc-cpu-core-1"]["value"], 54)
        self.assertEqual(by_id["voidpc-cpu-package"]["value"], 62)
        self.assertEqual(by_id["voidpc-gpu-hot-spot"]["value"], 71)
        self.assertEqual(by_id["voidpc-gpu-hot-spot"]["source"], "voidpc")


class FakeHttpResponse:
    def __init__(self, body="{}", status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body.encode("utf-8")

    def getcode(self):
        return self.status


class CurveEngineTests(unittest.TestCase):
    def test_interpolates_between_curve_points(self):
        engine = CurveEngine(None, None, None)
        pwm = engine.interpolate_pwm(
            [{"temp": 30, "pwm": 20}, {"temp": 70, "pwm": 100}],
            50,
        )
        self.assertEqual(pwm, 60)

    def test_calculates_popular_curve_types(self):
        engine = CurveEngine(None, None, None)
        points = [{"temp": 40, "pwm": 30}, {"temp": 70, "pwm": 90}]

        self.assertEqual(engine.calculate_pwm("linear", points, 55), 60)
        self.assertEqual(engine.calculate_pwm("flat", points, 55), 30)
        self.assertEqual(engine.calculate_pwm("step", points, 55), 30)
        self.assertEqual(engine.calculate_pwm("step", points, 75), 90)
        self.assertEqual(engine.calculate_pwm("trigger", points, 69), 30)
        self.assertEqual(engine.calculate_pwm("trigger", points, 70), 90)

    def test_flat_curve_applies_without_sensors(self):
        class FakeHardware:
            def __init__(self):
                self.calls = []

            def set_fan_pwm(self, controller_id, fan, pwm):
                self.calls.append((controller_id, fan, pwm))

        store = FanControlConfigStore(path=None)
        store.upsert_curve(
            {
                "name": "fixed",
                "curve_type": "flat",
                "sensor_ids": [],
                "targets": [{"controller_id": "serial-1", "fan": "1"}],
                "points": [{"temp": 0, "pwm": 42}],
            }
        )
        sensors = SimpleNamespace(read_sensors=lambda: [])
        hardware = FakeHardware()
        engine = CurveEngine(hardware, store, sensors)

        result = engine.evaluate_once()[0]

        self.assertEqual(hardware.calls, [("serial-1", 1, 42.0)])
        self.assertIsNone(result["last_value"])
        self.assertEqual(result["last_pwm"], 42.0)
        self.assertIsNone(result["last_error"])

    def test_store_saves_controller_names_and_curve_type(self):
        store = FanControlConfigStore(path=None)
        store.set_controller_name("serial-1", "Top Radiator")
        curve = store.upsert_curve(
            {
                "name": "gpu-curve",
                "curve_type": "step",
                "sensor_ids": ["voidpc-gpu"],
                "targets": [
                    {"controller_id": "serial-1", "fan": "1"},
                    {"controller_id": "serial-1", "fan": "2"},
                ],
                "points": [{"temp": 40, "pwm": 30}, {"temp": 70, "pwm": 90}],
                "hysteresis": 3.5,
                "response_time": 1.25,
            }
        )

        self.assertEqual(store.get_controller_name("serial-1"), "Top Radiator")
        self.assertEqual(curve.curve_type, "step")
        self.assertEqual(curve.hysteresis, 3.5)
        self.assertEqual(curve.response_time, 1.25)
        self.assertEqual([target.fan for target in curve.targets], ["1", "2"])

    def test_store_defaults_hysteresis_and_response_time(self):
        store = FanControlConfigStore(path=None)
        curve = store.upsert_curve(
            {
                "name": "default tuning",
                "curve_type": "linear",
                "sensor_ids": ["voidpc-cpu"],
                "targets": [{"controller_id": "serial-1", "fan": "1"}],
                "points": [{"temp": 40, "pwm": 30}],
            }
        )

        self.assertEqual(curve.hysteresis, 2.0)
        self.assertEqual(curve.response_time, 1.0)

    def test_store_rejects_sensor_group_used_by_another_switch(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        store.upsert_power_switch(
            {
                "name": "switch-a",
                "url": "http://switch-a.local",
                "sensor_groups": ["voidpc"],
            }
        )

        with self.assertRaises(ValueError):
            store.upsert_power_switch(
                {
                    "name": "switch-b",
                    "url": "http://switch-b.local",
                    "sensor_groups": ["voidpc"],
                }
            )

    def test_store_upserts_power_switches_by_name(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        first = store.upsert_power_switch(
            {
                "name": "Desk Switch",
                "url": "http://switch.local",
                "sensor_groups": ["voidpc"],
            }
        )
        second = store.upsert_power_switch(
            {
                "name": "desk switch",
                "url": "http://switch.local/power",
                "sensor_groups": [],
            }
        )

        switches = store.snapshot_power_switches()
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0].url, "http://switch.local/power")
        self.assertEqual(switches[0].action_url("on"), "http://switch.local/power/on")
        self.assertEqual(switches[0].sensor_groups, [])

    def test_store_infers_base_switch_url_from_legacy_action_url(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        switch = store.upsert_power_switch(
            {
                "name": "legacy",
                "on_url": "http://switch.local/on",
                "sensor_groups": ["voidpc"],
            }
        )

        self.assertEqual(switch.url, "http://switch.local")
        self.assertEqual(switch.action_url("state"), "http://switch.local/state")

    def test_power_switch_sends_command_and_reads_actual_state(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        store.upsert_power_switch(
            {
                "name": "desk",
                "url": "http://switch.local",
                "sensor_groups": ["voidpc"],
            }
        )
        sensors = SimpleNamespace(last_ok_sources={"voidpc"}, read_sensors=lambda: [])
        engine = CurveEngine(None, store, sensors)
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            if request.full_url.endswith("/state"):
                return FakeHttpResponse('{"state":"on"}')
            return FakeHttpResponse("")

        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            result = engine.evaluate_power_switches()[0]

        self.assertEqual(calls, ["http://switch.local/on", "http://switch.local/state"])
        self.assertEqual(result["desired_state"], "on")
        self.assertEqual(result["state"], "on")
        self.assertEqual(result["last_command"], "on")
        self.assertIsNone(result["last_error"])

    def test_power_switch_sends_off_without_working_assigned_groups(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        store.upsert_power_switch(
            {
                "name": "desk",
                "url": "http://switch.local",
                "sensor_groups": ["voidpc"],
            }
        )
        sensors = SimpleNamespace(last_ok_sources=set(), read_sensors=lambda: [])
        engine = CurveEngine(None, store, sensors)
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            if request.full_url.endswith("/state"):
                return FakeHttpResponse("off")
            return FakeHttpResponse("")

        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            result = engine.evaluate_power_switches()[0]

        self.assertEqual(calls, ["http://switch.local/off", "http://switch.local/state"])
        self.assertEqual(result["desired_state"], "off")
        self.assertEqual(result["state"], "off")
        self.assertEqual(result["last_command"], "off")

    def test_power_switch_state_read_uses_its_own_interval(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        switch = store.upsert_power_switch(
            {
                "name": "desk",
                "url": "http://switch.local",
                "sensor_groups": ["voidpc"],
            }
        )
        now = time.time()
        store.set_power_switch_status(
            switch.id,
            state="on",
            desired_state="on",
            last_command="on",
            last_command_at=now,
            last_state_read_at=now,
        )
        sensors = SimpleNamespace(last_ok_sources={"voidpc"}, read_sensors=lambda: [])
        engine = CurveEngine(None, store, sensors, power_switch_state_interval=30)
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            return FakeHttpResponse("on")

        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            result = engine.evaluate_power_switches()[0]

        self.assertEqual(calls, [])
        self.assertEqual(result["state"], "on")

        store.set_power_switch_status(
            switch.id,
            state="on",
            desired_state="on",
            last_command="on",
            last_command_at=now,
            last_state_read_at=now - 31,
        )
        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            result = engine.evaluate_power_switches()[0]

        self.assertEqual(calls, ["http://switch.local/state"])
        self.assertEqual(result["state"], "on")

    def test_power_switch_does_not_retry_from_stale_state_before_next_state_read(self):
        store = FanControlConfigStore(path=None)
        store.add_source("voidpc", "http://sensor.local/api")
        switch = store.upsert_power_switch(
            {
                "name": "desk",
                "url": "http://switch.local",
                "sensor_groups": ["voidpc"],
            }
        )
        now = time.time()
        store.set_power_switch_status(
            switch.id,
            state="off",
            desired_state="on",
            last_command="on",
            last_command_at=now,
            last_state_read_at=now - 10,
        )
        sensors = SimpleNamespace(last_ok_sources={"voidpc"}, read_sensors=lambda: [])
        engine = CurveEngine(None, store, sensors, power_switch_state_interval=30)
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            return FakeHttpResponse("")

        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            engine.evaluate_power_switches()[0]

        self.assertEqual(calls, [])

        store.set_power_switch_status(
            switch.id,
            state="off",
            desired_state="on",
            last_command="on",
            last_command_at=now,
            last_state_read_at=now + 1,
        )
        with patch("fan_control_service.urllib.request.urlopen", fake_urlopen):
            engine.evaluate_power_switches(read_state=False)[0]

        self.assertEqual(calls, ["http://switch.local/on"])

    def test_curve_engine_holds_pwm_inside_hysteresis_and_response_time(self):
        class FakeHardware:
            def __init__(self):
                self.calls = []

            def set_fan_pwm(self, controller_id, fan, pwm):
                self.calls.append((controller_id, fan, pwm))

        readings = [
            [{"id": "voidpc-cpu", "value": 50}],
            [{"id": "voidpc-cpu", "value": 51}],
            [{"id": "voidpc-cpu", "value": 55}],
            [{"id": "voidpc-cpu", "value": 55}],
        ]

        def read_sensors():
            return readings.pop(0)

        store = FanControlConfigStore(path=None)
        store.upsert_curve(
            {
                "name": "quiet",
                "curve_type": "linear",
                "sensor_ids": ["voidpc-cpu"],
                "targets": [{"controller_id": "serial-1", "fan": "1"}],
                "points": [{"temp": 40, "pwm": 30}, {"temp": 60, "pwm": 70}],
                "hysteresis": 2,
                "response_time": 3,
            }
        )
        hardware = FakeHardware()
        engine = CurveEngine(hardware, store, SimpleNamespace(read_sensors=read_sensors))

        with patch("fan_control_service.time.time", side_effect=[0, 1, 2, 4]):
            self.assertEqual(engine.evaluate_once()[0]["last_pwm"], 50.0)
            self.assertEqual(engine.evaluate_once()[0]["last_pwm"], 50.0)
            self.assertEqual(engine.evaluate_once()[0]["last_pwm"], 50.0)
            self.assertEqual(engine.evaluate_once()[0]["last_pwm"], 60.0)

        self.assertEqual(
            hardware.calls,
            [
                ("serial-1", 1, 50.0),
                ("serial-1", 1, 60.0),
            ],
        )

    def test_store_upserts_curves_by_name(self):
        store = FanControlConfigStore(path=None)
        first = store.upsert_curve(
            {
                "name": "Main Curve",
                "curve_type": "linear",
                "sensor_ids": ["voidpc-cpu"],
                "targets": [{"controller_id": "serial-1", "fan": "all"}],
                "points": [{"temp": 40, "pwm": 30}],
            }
        )
        second = store.upsert_curve(
            {
                "name": "main curve",
                "curve_type": "flat",
                "sensor_ids": ["voidpc-gpu"],
                "targets": [{"controller_id": "serial-2", "fan": "1"}],
                "points": [{"temp": 50, "pwm": 55}],
            }
        )

        curves = store.snapshot_curves()
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(curves), 1)
        self.assertEqual(curves[0].curve_type, "flat")
        self.assertEqual(curves[0].sensor_ids, ["voidpc-gpu"])

    def test_store_rejects_fan_target_used_by_another_curve(self):
        store = FanControlConfigStore(path=None)
        store.upsert_curve(
            {
                "name": "CPU Curve",
                "curve_type": "linear",
                "sensor_ids": ["voidpc-cpu"],
                "targets": [{"controller_id": "serial-1", "fan": "1"}],
                "points": [{"temp": 40, "pwm": 30}],
            }
        )

        with self.assertRaises(ValueError):
            store.upsert_curve(
                {
                    "name": "GPU Curve",
                    "curve_type": "linear",
                    "sensor_ids": ["voidpc-gpu"],
                    "targets": [{"controller_id": "serial-1", "fan": "1"}],
                    "points": [{"temp": 50, "pwm": 60}],
                }
            )

        with self.assertRaises(ValueError):
            store.upsert_curve(
                {
                    "name": "All Curve",
                    "curve_type": "linear",
                    "sensor_ids": ["voidpc-gpu"],
                    "targets": [{"controller_id": "serial-1", "fan": "all"}],
                    "points": [{"temp": 50, "pwm": 60}],
                }
            )


if __name__ == "__main__":
    unittest.main()

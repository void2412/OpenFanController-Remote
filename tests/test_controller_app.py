import os
from pathlib import Path
import unittest
from unittest.mock import patch

import controller_app


class ControllerAppSettingsTests(unittest.TestCase):
    def test_split_poll_intervals_are_loaded_from_env(self):
        env = {
            "HOST": "0.0.0.0",
            "PORT": "8090",
            "CONTROLLER_POLL_INTERVAL": "5.0",
            "CURVE_POLL_INTERVAL": "1.5",
            "POWER_SWITCH_STATE_INTERVAL": "7.0",
            "LOG_LEVEL": "debug",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = controller_app.load_settings(env_path=Path("missing.env"))

        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8090)
        self.assertEqual(settings.controller_poll_interval, 5.0)
        self.assertEqual(settings.curve_poll_interval, 1.5)
        self.assertEqual(settings.power_switch_state_interval, 7.0)
        self.assertEqual(settings.log_level, "debug")

    def test_shared_poll_interval_is_used_as_fallback(self):
        with patch.dict(os.environ, {"POLL_INTERVAL": "4.0"}, clear=True):
            settings = controller_app.load_settings(env_path=Path("missing.env"))

        self.assertEqual(settings.controller_poll_interval, 4.0)
        self.assertEqual(settings.curve_poll_interval, 4.0)
        self.assertEqual(settings.power_switch_state_interval, 4.0)

    def test_old_prefixed_env_names_still_work_as_fallback(self):
        env = {
            "OFC_HOST": "0.0.0.0",
            "OFC_PORT": "8090",
            "OFC_CONTROLLER_POLL_INTERVAL": "5.0",
            "OFC_CURVE_POLL_INTERVAL": "1.5",
            "OFC_POWER_SWITCH_STATE_INTERVAL": "7.0",
            "OFC_LOG_LEVEL": "debug",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = controller_app.load_settings(env_path=Path("missing.env"))

        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8090)
        self.assertEqual(settings.controller_poll_interval, 5.0)
        self.assertEqual(settings.curve_poll_interval, 1.5)
        self.assertEqual(settings.power_switch_state_interval, 7.0)
        self.assertEqual(settings.log_level, "debug")


if __name__ == "__main__":
    unittest.main()

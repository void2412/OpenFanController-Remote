import os
from pathlib import Path
import unittest
from unittest.mock import patch

import controller_app


class ControllerAppSettingsTests(unittest.TestCase):
    def test_split_poll_intervals_are_loaded_from_env(self):
        env = {
            "OFC_HOST": "0.0.0.0",
            "OFC_PORT": "8090",
            "OFC_CONTROLLER_POLL_INTERVAL": "5.0",
            "OFC_CURVE_POLL_INTERVAL": "1.5",
            "OFC_LOG_LEVEL": "debug",
        }

        with patch.dict(os.environ, env, clear=True):
            settings = controller_app.load_settings(env_path=Path("missing.env"))

        self.assertEqual(settings.host, "0.0.0.0")
        self.assertEqual(settings.port, 8090)
        self.assertEqual(settings.controller_poll_interval, 5.0)
        self.assertEqual(settings.curve_poll_interval, 1.5)
        self.assertEqual(settings.log_level, "debug")

    def test_old_poll_interval_is_used_as_fallback(self):
        with patch.dict(os.environ, {"OFC_POLL_INTERVAL": "4.0"}, clear=True):
            settings = controller_app.load_settings(env_path=Path("missing.env"))

        self.assertEqual(settings.controller_poll_interval, 4.0)
        self.assertEqual(settings.curve_poll_interval, 4.0)


if __name__ == "__main__":
    unittest.main()

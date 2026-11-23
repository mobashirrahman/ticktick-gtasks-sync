import unittest

from ttgtsync import config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = config.load(None)
        self.assertEqual(cfg["duration"]["default_minutes"], 30)
        self.assertEqual(cfg["conflicts"], "newest")
        self.assertEqual(cfg["recurring"], "series")

    def test_rejects_unknown_modes(self):
        with self.assertRaises(ValueError):
            config._validate({**config.DEFAULTS, "on_delete": "explode"})
        with self.assertRaises(ValueError):
            config._validate({**config.DEFAULTS, "conflicts": "flip-a-coin"})
        with self.assertRaises(ValueError):
            config._validate({**config.DEFAULTS, "recurring": "explode"})

    def test_rejects_unknown_timezone(self):
        cfg = config._merge(config.DEFAULTS, {"google": {"calendar": {"timezone": "Europe/Berln"}}})
        with self.assertRaises(ValueError):
            config._validate(cfg)

    def test_rejects_unknown_oauth_app_status(self):
        cfg = config._merge(config.DEFAULTS, {"google": {"oauth_app_status": "live"}})
        with self.assertRaises(ValueError):
            config._validate(cfg)

    def test_refresh_expiry_warning_only_in_testing(self):
        testing = config.load(None)
        published = config._merge(testing, {"google": {"oauth_app_status": "published"}})
        self.assertTrue(config.google_refresh_expiring(testing, 6.6))
        self.assertFalse(config.google_refresh_expiring(testing, 3.0))
        self.assertFalse(config.google_refresh_expiring(testing, None))
        self.assertFalse(config.google_refresh_expiring(published, 30.0))

    def test_duration_precedence(self):
        cfg = config.load(None)
        self.assertEqual(config.duration_minutes(cfg, {"tags": ["#15min"]}), 15)
        self.assertEqual(config.duration_minutes(cfg, {"tags": [], "title": "review 45min"}), 45)
        self.assertEqual(config.duration_minutes(cfg, {"tags": []}), 30)


if __name__ == "__main__":
    unittest.main()

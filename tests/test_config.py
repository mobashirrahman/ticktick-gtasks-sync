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

    def test_duration_precedence(self):
        cfg = config.load(None)
        self.assertEqual(config.duration_minutes(cfg, {"tags": ["#15min"]}), 15)
        self.assertEqual(config.duration_minutes(cfg, {"tags": [], "title": "review 45min"}), 45)
        self.assertEqual(config.duration_minutes(cfg, {"tags": []}), 30)


if __name__ == "__main__":
    unittest.main()

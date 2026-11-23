import unittest

from ttgtsync.duration import (
    duration_tag,
    find_minutes,
    plain_tags,
    replace_duration_tag,
    tag_minutes,
)


class DurationTests(unittest.TestCase):
    def test_tag_units(self):
        cases = {
            "#15min": 15, "#30m": 30, "#1hr": 60, "#1hour": 60,
            "#2hrs": 120, "#60min": 60, "#45minutes": 45,
        }
        for tag, expected in cases.items():
            with self.subTest(tag=tag):
                self.assertEqual(tag_minutes(tag), expected)

    def test_non_duration_tags(self):
        for tag in ["#routine", "#work", "routine", "#"]:
            with self.subTest(tag=tag):
                self.assertIsNone(tag_minutes(tag))

    def test_find_from_tags(self):
        self.assertEqual(find_minutes(["#routine", "#60min"]), 60)
        self.assertEqual(find_minutes(["#15min", "#60min"]), 15)

    def test_find_from_text_then_default(self):
        self.assertEqual(find_minutes([], "budget review 20min"), 20)
        self.assertEqual(find_minutes([], "", default=30), 30)

    def test_clamped_to_maximum(self):
        self.assertEqual(find_minutes(["#600min"], maximum=480), 480)

    def test_replace_duration_tag(self):
        self.assertEqual(replace_duration_tag(["#routine", "#60min"], 45), ["#routine", "#45min"])
        self.assertEqual(replace_duration_tag(["#routine"], 45), ["#routine", "#45min"])
        self.assertEqual(replace_duration_tag(["#15min", "#60min"], 45), ["#45min"])

    def test_helpers(self):
        self.assertEqual(duration_tag(45), "#45min")
        self.assertEqual(plain_tags(["#routine", "#60min", "#deep"]), ["#routine", "#deep"])


if __name__ == "__main__":
    unittest.main()

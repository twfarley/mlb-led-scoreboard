"""Tests for `layout_variant`, which selects an alternative layout for a board size.

The point of the option is that shipping an alternative arrangement should not
require editing or renaming an example file. Both matter:

  * `coordinates/README.md` tells users not to touch `.example.json` files, and the
    software reads them to decide which dimensions are supported.
  * a custom `wXXXhYY.json` is reconciled against `wXXXhYY.example.json` by
    validate_config.py, which *deletes* keys the example does not have. Copying an
    alternative layout over the stock filename would therefore have every key
    specific to it stripped on the next update -- including, worst of all, the
    inning arrow's absolute coordinates being silently swapped back for offsets.
    Naming the variant keeps a custom file paired with the example it came from.
"""

import unittest

from data.paths import COORDINATES_DIRECTORY
from tests.helpers import make_test_config

VARIANT = "VERBOSE"
VARIANT_SIZE = (128, 64)


class TestLayoutVariant(unittest.TestCase):
    def test_the_default_is_the_stock_layout_for_the_board_size(self):
        config = make_test_config(led_cols=128, led_rows=64)
        self.assertEqual(config.layout_variant, "")
        stock = config.layout.json
        self.assertNotIn("record", stock["final"], "the stock 128x64 layout should be untouched")

    def test_a_variant_loads_its_own_example_file(self):
        config = make_test_config(led_cols=128, led_rows=64)
        config.layout_variant = VARIANT
        config.set_layout(*VARIANT_SIZE)
        self.assertIn("record", config.layout.json["final"], "the verbose layout should be in use")

    def test_the_variant_example_exists_for_the_size_it_names(self):
        """A variant is only usable if its example is present, since that is the file
        the software reads to decide the size is supported."""
        width, height = VARIANT_SIZE
        example = COORDINATES_DIRECTORY / f"w{width}h{height}{VARIANT}.example.json"
        self.assertTrue(example.is_file(), f"{example.name} is missing")

    def test_a_variant_that_does_not_exist_exits_rather_than_falling_back(self):
        """Silently rendering the stock layout would look like the option was
        ignored, and the option is the only way to tell which layout is loaded."""
        config = make_test_config(led_cols=128, led_rows=64)
        config.layout_variant = "NOPE"
        with self.assertRaises(SystemExit):
            config.set_layout(*VARIANT_SIZE)


if __name__ == "__main__":
    unittest.main()

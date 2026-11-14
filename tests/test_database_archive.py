import tempfile
import unittest
from pathlib import Path

import database


class DatabaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = database.DB_PATH
        database.DB_PATH = str(Path(self.temp_dir.name) / "test_deals.db")

    def tearDown(self):
        database.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def item(self, price=100.0):
        return {
            "store": "Test Store",
            "title": "Test Product",
            "url": "https://shop.example/product/test?tracking=1",
            "price": price,
        }

    def test_first_product_is_archived(self):
        item = self.item()
        result = database.save_and_evaluate(item)
        self.assertIsNone(result)
        self.assertTrue(item["_is_new_product"])
        self.assertTrue(item["_price_recorded"])
        stats = database.get_archive_stats()
        self.assertEqual(stats["products"], 1)
        self.assertEqual(stats["price_observations"], 1)
        self.assertEqual(stats["stores_with_products"], 1)

    def test_same_price_is_not_written_every_15_minutes(self):
        first = self.item()
        database.save_and_evaluate(first)
        second = self.item()
        database.save_and_evaluate(second)
        self.assertFalse(second["_is_new_product"])
        self.assertFalse(second["_price_recorded"])
        self.assertEqual(database.get_archive_stats()["price_observations"], 1)

    def test_price_change_is_recorded_immediately(self):
        first = self.item(100)
        database.save_and_evaluate(first)
        second = self.item(75)
        database.save_and_evaluate(second)
        self.assertTrue(second["_price_recorded"])
        self.assertEqual(database.get_archive_stats()["price_observations"], 2)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock, patch

from scrapers.common import parse_jsonld_products
from telegram_bot import _send_text


class AmazonDeliveryTests(unittest.TestCase):
    url = "https://www.amazon.sa/dp/B000000001"
    detail = """
        <input id="ASIN" value="B000000001">
        <h1 id="productTitle">Wireless gaming mouse</h1>
        <div id="corePrice_feature_div">
          <span class="priceToPay"><span class="a-offscreen">SAR 75</span></span>
          <span class="a-text-price"><span class="a-offscreen">SAR 120</span></span>
        </div>
    """

    def test_tracked_detail_has_current_and_reference_prices(self):
        item, = parse_jsonld_products(self.detail, self.url, "Amazon.sa")
        self.assertEqual((item["price"], item["original_price"]), (75, 120))

    def test_other_variant_is_ignored(self):
        result = parse_jsonld_products(self.detail, self.url[:-1] + "2", "Amazon.sa")
        self.assertEqual(result, [])

    def test_telegram_retries_explicit_rate_limit(self):
        limited = Mock(status_code=429)
        limited.json.return_value = {"parameters": {"retry_after": 2}}
        sent = Mock(status_code=200, ok=True)
        sent.json.return_value = {"ok": True}
        with patch("telegram_bot.TG_TOKEN", "test"), patch("telegram_bot.TG_CHAT", "test"), \
                patch("telegram_bot.requests.post", side_effect=[limited, sent]) as post, \
                patch("telegram_bot.time.sleep") as sleep:
            self.assertTrue(_send_text("test")["ok"])
        sleep.assert_called_once_with(2)
        self.assertEqual(post.call_count, 2)

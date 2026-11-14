import unittest

from scrapers.common import parse_jsonld_products


class ProductParserTests(unittest.TestCase):
    def test_jsonld_product(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Product","name":"USB C Charger 100W",
         "url":"/charger-100w","offers":{"price":"149.00","url":"/charger-100w"},
         "aggregateRating":{"ratingValue":"4.6","reviewCount":"128"}}
        </script>
        """
        items = parse_jsonld_products(html, "https://shop.example/search", "Amazon.sa")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["price"], 149.0)
        self.assertEqual(items[0]["rating"], 4.6)
        self.assertEqual(items[0]["reviews"], 128)

    def test_embedded_json_without_jsonld_type(self):
        html = """
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"products":[
          {"productName":"Wireless Mouse","salePrice":"79.90","productUrl":"/p/mouse"}
        ]}}}
        </script>
        """
        items = parse_jsonld_products(html, "https://www.noon.com/search", "Noon")
        self.assertTrue(any(x["title"] == "Wireless Mouse" and x["price"] == 79.9 for x in items))


    def test_embedded_nested_price_object(self):
        html = """
        <script type="application/json">
        {"products":[
          {"name":"Smart Plug","price":{"value":"59.50","currency":"SAR"},"url":"/products/plug"}
        ]}
        </script>
        """
        items = parse_jsonld_products(html, "https://shop.example/search", "Extra")
        self.assertTrue(any(x["title"] == "Smart Plug" and x["price"] == 59.5 for x in items))

    def test_amazon_html_card(self):
        html = """
        <div data-component-type="s-search-result" data-asin="B000000001" data-index="1">
          <h2><a href="/dp/B000000001"><span>Gaming Headset</span></a></h2>
          <span class="a-price"><span class="a-offscreen">SAR 299.00</span></span>
        </div>
        """
        items = parse_jsonld_products(html, "https://www.amazon.sa/s?k=headset", "Amazon.sa")
        self.assertTrue(any(x["title"] == "Gaming Headset" and x["price"] == 299.0 for x in items))

    def test_generic_product_link_ancestor(self):
        html = """
        <article class="tile">
          <a href="/p/keyboard"><span>Mechanical Keyboard</span></a>
          <div class="selling-price">399 SAR</div>
        </article>
        """
        items = parse_jsonld_products(html, "https://www.extra.com/search", "Extra")
        self.assertTrue(any(x["title"] == "Mechanical Keyboard" and x["price"] == 399.0 for x in items))


    def test_arabic_indic_price(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Product","name":"Arabic Price Charger",
         "url":"/arabic-charger","offers":{"price":"١٬٢٩٩٫٠٠"}}
        </script>
        """
        items = parse_jsonld_products(html, "https://shop.example/search", "Noon")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["price"], 1299.0)

    def test_meta_product_fallback(self):
        html = """
        <html><head>
          <meta property="og:title" content="Smart Watch">
          <meta property="product:price:amount" content="249.00">
          <link rel="canonical" href="https://shop.example/watch">
        </head></html>
        """
        items = parse_jsonld_products(html, "https://shop.example/search", "Jarir")
        self.assertTrue(any(x["title"] == "Smart Watch" and x["price"] == 249.0 for x in items))


if __name__ == "__main__":
    unittest.main()

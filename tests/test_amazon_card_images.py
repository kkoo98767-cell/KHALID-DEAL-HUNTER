import unittest

from scrapers.common import parse_jsonld_products


def card(asin,title,picture,price):
    return f'''<div data-component-type="s-search-result" data-asin="{asin}">
      <div data-cy="image-container"><span><a href="/dp/{asin}/ref=sr_1">
        <div><img class="s-image" src="{picture}"/></div></a></span></div>
      <div><div data-cy="title-recipe"><a href="/dp/{asin}/ref=sr_1">
        <h2 aria-label="{title}"><span>{title}</span></h2></a></div>
      <div data-cy="price-recipe"><div><div><a href="/dp/{asin}/ref=sr_1">
        <span class="a-price"><span class="a-offscreen">SAR {price}</span></span>
      </a></div></div></div></div>
    </div>'''


class AmazonCardImageTests(unittest.TestCase):
    def test_heading_after_empty_image_link_keeps_matching_card_image(self):
        picture="https://m.media-amazon.com/images/I/51zuUAZoxtL._AC_UL320_.jpg"
        html=card("B0DJQTFSPJ","Power bank 20000mAh",picture,61.99)
        products=parse_jsonld_products(html,"https://www.amazon.sa/s?k=power+bank","Amazon.sa")
        self.assertEqual(len(products),1)
        self.assertEqual(products[0]["image_url"],picture)
        self.assertEqual(products[0]["price"],61.99)
        self.assertIn("html_card",products[0]["data_sources"])

    def test_neighbor_product_image_is_never_borrowed(self):
        first="https://m.media-amazon.com/images/I/first.jpg"
        second="https://m.media-amazon.com/images/I/second.jpg"
        html=card("B000000001","First power bank",first,100)+card("B000000002","Second power bank",second,200)
        products=parse_jsonld_products(html,"https://www.amazon.sa/s?k=power+bank","Amazon.sa")
        by_id={p["product_id"]:p for p in products}
        self.assertEqual(by_id["B000000001"]["image_url"],first)
        self.assertEqual(by_id["B000000002"]["image_url"],second)
        self.assertEqual(by_id["B000000002"]["price"],200)

    def test_missing_image_is_not_guessed(self):
        html=card("B000000001","Power bank", "",100)
        products=parse_jsonld_products(html,"https://www.amazon.sa/s?k=power+bank","Amazon.sa")
        self.assertNotIn("image_url",products[0])

    def test_product_image_precedes_decorative_badge_in_selector_priority(self):
        picture="https://m.media-amazon.com/images/I/product.jpg"
        html=card("B000000001","Power bank",picture,100).replace('<div data-cy="image-container">','<img src="https://m.media-amazon.com/images/I/badge.jpg"/><div data-cy="image-container">')
        products=parse_jsonld_products(html,"https://www.amazon.sa/s?k=power+bank","Amazon.sa")
        self.assertEqual(products[0]["image_url"],picture)


if __name__=="__main__":
    unittest.main()

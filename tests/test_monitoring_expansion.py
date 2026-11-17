import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import requests
import database
import main
import discovery
from config import STORES
from scrapers.jarir import discover,PublicSourceError
from scrapers.common import parse_jsonld_products


def item(code,price=100,original=200):
    return dict(store="Amazon.sa",title="Wireless Mouse",url="https://www.amazon.sa/dp/"+code,price=price,original_price=original)


def response(html,url="https://www.amazon.sa/s?k=mouse",status=200):
    return Mock(status_code=status,text=html,content=html.encode(),url=url,headers={})


class ExpandedMonitoringTests(unittest.TestCase):
    def test_only_amazon_and_jarir_are_active(self):
        self.assertEqual(set(STORES),{"Amazon.sa","Jarir"})
        with self.assertRaises(ValueError):main.fetch_store("Noon","mouse")
        with self.assertRaises(ValueError):discovery.choose_search("Extra")

    def test_four_queries_collect_more_than_old_twenty_cap(self):
        batches=[[item(f"B{i*30+j:09d}") for j in range(30)] for i in range(4)]
        with patch.object(main,"fetch_store",side_effect=batches),patch.object(main.time,"sleep"):
            products,report=main.collect_store("Amazon.sa",["mouse","keyboard","headphones","charger"])
        self.assertEqual(len(products),120)
        self.assertEqual(report["queries_checked"],4)
        self.assertIsNone(report["error"])

    def test_duplicate_queries_do_not_duplicate_products(self):
        with patch.object(main,"fetch_store",return_value=[item("B000000001")]),patch.object(main.time,"sleep"):
            products,report=main.collect_store("Amazon.sa",["mouse","wireless mouse"])
        self.assertEqual(len(products),1)
        self.assertEqual(report["queries_checked"],2)

    def test_conflicting_prices_are_held_back(self):
        with patch.object(main,"fetch_store",side_effect=[[item("B000000001",100)],[item("B000000001",150)]]),patch.object(main.time,"sleep"):
            products,report=main.collect_store("Amazon.sa",["mouse","wireless mouse"])
        self.assertEqual(products,[])
        self.assertEqual(report["conflicting_products"],1)

    def test_later_access_denial_keeps_already_collected_products(self):
        partial=main.StoreReadError("access denied",[item("B000000002")],blocked=True)
        with patch.object(main,"fetch_store",side_effect=[[item("B000000001")],partial]) as fetch,patch.object(main.time,"sleep"):
            products,report=main.collect_store("Amazon.sa",["mouse","keyboard","charger"])
        self.assertEqual(len(products),2)
        self.assertEqual(fetch.call_count,2)
        self.assertIsNotNone(report["error"])

    def test_advertised_candidates_are_prioritized_without_discarding_normal_products(self):
        normal=item("B000000001",150,160);candidate=item("B000000002",100,200)
        with patch.object(main,"fetch_store",return_value=[normal,candidate]):
            products,_=main.collect_store("Amazon.sa",["mouse"])
        self.assertEqual(products[0]["url"],candidate["url"])
        self.assertEqual(len(products),2)

    def test_html_next_page_is_followed_without_url_list_mutation(self):
        first='<div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Mouse One</a></h2><span class="a-price"><span class="a-offscreen">SAR 100</span></span></div><a rel="next" href="/s?k=mouse&page=2">Next</a>'
        second='<div data-component-type="s-search-result"><h2><a href="/dp/B000000002">Mouse Two</a></h2><span class="a-price"><span class="a-offscreen">SAR 100</span></span></div>'
        with patch.object(main.requests.Session,"get",side_effect=[response(first),response(second,"https://www.amazon.sa/s?k=mouse&page=2")]) as get,patch.object(main.time,"sleep"):
            products=main.fetch_store("Amazon.sa","mouse")
        self.assertEqual(len(products),2)
        self.assertEqual(get.call_count,2)

    def test_failed_later_html_page_preserves_validated_first_page(self):
        html='<div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Mouse One</a></h2><span class="a-price"><span class="a-offscreen">SAR 100</span></span></div><a rel="next" href="/s?k=mouse&page=2">Next</a>'
        with patch.object(main.requests.Session,"get",side_effect=[response(html),requests.Timeout(),requests.Timeout(),requests.Timeout()]),patch.object(main.time,"sleep"):
            with self.assertRaises(main.StoreReadError) as error:main.fetch_store("Amazon.sa","mouse")
        self.assertEqual(len(error.exception.products),1)

    def test_search_recovers_from_two_transient_503_responses(self):
        session = Mock()
        unavailable = response("Service unavailable", status=503)
        recovered = response("Recovered")
        session.get.side_effect = [unavailable, unavailable, recovered]
        with patch.object(main.time, "sleep") as sleep:
            self.assertIs(main._fetch_search_page(session, recovered.url), recovered)
        self.assertEqual(session.get.call_count, 3)
        self.assertGreater(sleep.call_args_list[1].args[0], sleep.call_args_list[0].args[0])

    def test_retry_after_beyond_scan_budget_stops_the_store(self):
        session = Mock()
        session.get.return_value = response("Service unavailable", status=503)
        session.get.return_value.headers["Retry-After"] = "120"
        with patch.object(main.time, "sleep") as sleep:
            with self.assertRaises(main.StoreReadError) as error:
                main._fetch_search_page(session, session.get.return_value.url)
        self.assertTrue(error.exception.blocked)
        self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    def test_http_200_empty_interstitial_is_not_a_successful_store_scan(self):
        with patch.object(main.requests.Session,"get",return_value=response('<html>Temporary page</html>')) as get,patch.object(main.time,"sleep"):
            with self.assertRaises(main.EmptyStoreResults):main.fetch_store("Amazon.sa","mouse")
        self.assertLessEqual(get.call_count,2)

    def test_collect_marks_empty_source_and_stops_after_two_attempts(self):
        with patch.object(main,"fetch_store",return_value=[]) as fetch,patch.object(main.time,"sleep"):
            products,report=main.collect_store("Amazon.sa",["mouse","keyboard","charger"])
        self.assertEqual(products,[])
        self.assertEqual(report["error"],"EmptyStoreResults")
        self.assertEqual(fetch.call_count,2)

    def test_amazon_automated_access_denial_is_not_retried_as_other_route(self):
        html='<html>To discuss automated access to Amazon data please contact api-services-support@amazon.com</html>'
        with patch.object(main.requests.Session,"get",return_value=response(html)) as get,patch.object(main.time,"sleep"):
            with self.assertRaises(main.StoreReadError) as error:main.fetch_store("Amazon.sa","mouse")
        self.assertTrue(error.exception.blocked)
        self.assertEqual(get.call_count,1)

    def test_discovery_chooses_distinct_persisted_queries(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(database,"DB_PATH",str(Path(temp)/"db.sqlite")):
            first=discovery.choose_queries("Jarir",4);second=discovery.choose_queries("Jarir",4)
            self.assertEqual(len(set(first+second)),8)
            db=database.connect();self.assertEqual(db.execute("SELECT COUNT(*) FROM discovery_history").fetchone()[0],8);db.close()

    def test_jarir_public_pagination_and_real_image_field(self):
        html='<script id="__NUXT_DATA__" type="application/json">'+json.dumps([{"$ssiteConfig:searchProviderKeys":1},{"en":2},"public-index-test"])+"</script>"
        offsets=[]
        def get(url,**kwargs):
            if "cnstrc.com" in url:
                offset=kwargs["params"]["offset"];offsets.append(offset)
                return Mock(status_code=200,json=lambda:{"response":{"total_num_results":40,"results":[{"data":{"id":str(100000+offset+i)}} for i in range(20)]}})
            skus=url.split("/sku/")[1].split("/")[0].split(",")
            return Mock(status_code=200,json=lambda:{"data":{"hits":{"hits":[{"_source":{"sku":sku,"name":"Wireless Mouse "+sku,"type_id":"simple","jarir_final_price":100,"regular_price":200,"url_path":"mouse-"+sku+".html","image":"https://ak-asset.jarir.com/real/"+sku+".jpg"}} for sku in skus]}}})
        session=Mock();session.get.side_effect=get
        with patch("time.sleep"):
            products=discover(session,html,"mouse",{},40)
        self.assertEqual(offsets,[0,20]);self.assertEqual(len(products),40)
        self.assertTrue(products[0]["image_url"].startswith("https://ak-asset.jarir.com/real/"))

    def test_jsonld_and_cards_merge_image_and_original_price(self):
        html='<script type="application/ld+json">{"@type":"Product","name":"Wireless Mouse","url":"/dp/B000000001","offers":{"price":100}}</script><div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Wireless Mouse</a></h2><img src="https://m.media-amazon.com/images/real.jpg"><span class="a-price"><span class="a-offscreen">SAR 100</span></span><span class="a-text-price"><span class="a-offscreen">SAR 200</span></span></div>'
        products=parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa",limit=1)
        self.assertEqual(len(products),1);self.assertEqual(products[0]["original_price"],200)
        self.assertEqual(products[0]["image_url"],"https://m.media-amazon.com/images/real.jpg")

    def test_jsonld_image_is_kept_without_invented_placeholder(self):
        html='<script type="application/ld+json">{"@type":"Product","name":"Wireless Mouse","url":"/dp/B000000001","image":{"url":"https://m.media-amazon.com/images/real.jpg"},"offers":{"price":100}}</script>'
        products=parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa")
        self.assertEqual(products[0]["image_url"],"https://m.media-amazon.com/images/real.jpg")
        html=html.replace('"image":{"url":"https://m.media-amazon.com/images/real.jpg"},','')
        self.assertNotIn("image_url",parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa")[0])


if __name__=="__main__":unittest.main()

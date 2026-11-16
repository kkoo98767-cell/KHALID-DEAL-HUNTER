import unittest
import tempfile
import json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
import database
from dashboard import snapshot
from deal_evidence import savings,valid_gtin,matching_offers,evaluate
from scrapers.jarir import parse_catalog,discover
import main


class EvidenceTests(unittest.TestCase):
    def product(self,**kw):
        p=dict(id="x",title="Valid Product",store="Amazon.sa",price=140,currency="SAR",baseline=200,history_days=9,status="VERIFIED_DEAL",last_seen=datetime.now(timezone.utc).isoformat(),product_id="B000000001",url="https://www.amazon.sa/dp/B000000001",gtin="4006381333931",condition="new",brand="Example")
        p.update(kw);return p

    def test_decimal_money(self):
        self.assertEqual(savings(199.99,149.99),dict(amount_sar=50.0,percent=25.0))

    def test_missing_is_not_zero(self):
        for ref in [None,0,float("nan"),-1]:self.assertEqual(savings(ref,100),dict(amount_sar=None,percent=None))

    def test_price_increase_has_no_positive_savings(self):
        self.assertEqual(savings(100,120),dict(amount_sar=0.0,percent=0.0))

    def test_claim_not_database_authority(self):
        e=evaluate(self.product(price=180,store_original_price=300,status="VERIFIED_DEAL"),[])
        self.assertEqual(e["historical_savings"],dict(amount_sar=20,percent=10))
        self.assertEqual(e["advertised_savings"],dict(amount_sar=120,percent=40))
        self.assertEqual(e["claim_assessment"],"OVERSTATED_VS_HISTORY")
        self.assertTrue(e["verified_deal"])

    def test_insufficient_history_caps_trust_and_never_verifies(self):
        e=evaluate(self.product(baseline=None,status="INSUFFICIENT_HISTORY",store_original_price=500),[])
        self.assertLessEqual(e["trust_score"],39);self.assertFalse(e["historical_evidence_valid"])
        self.assertIsNone(e["historical_savings"]["percent"])

    def test_stale_price_revokes_verification(self):
        e=evaluate(self.product(last_seen=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()),[])
        self.assertFalse(e["verified_deal"]);self.assertLessEqual(e["trust_score"],39)

    def test_anomaly_zero_trust(self):
        self.assertEqual(evaluate(self.product(status="DATA_ANOMALY"),[])["trust_score"],0)

    def test_gtin_checksum(self):
        self.assertTrue(valid_gtin("4006381333931"))
        for gtin in ["4006381333932","0000000000000","123",None]:self.assertFalse(valid_gtin(gtin))

    def test_cross_store_exact_identifier(self):
        p=self.product();other=self.product(id="y",store="Jarir",price=130,url="https://www.jarir.com/sa-en/x.html")
        offers=matching_offers(p,[other]);self.assertEqual(len(offers),1)
        self.assertTrue(offers[0]["condition_confirmed"])
        self.assertFalse(offers[0]["shipping_included"])
        self.assertEqual(evaluate(p,offers)["trust_score"],100)

    def test_cross_store_variant_mismatch_rejected(self):
        p=self.product(color="black");other=self.product(store="Jarir",color="white")
        self.assertEqual(matching_offers(p,[other]),[])

    def test_same_name_without_gtin_not_compared(self):
        self.assertEqual(matching_offers(self.product(gtin=None),[self.product(store="Jarir")]),[])

    def test_uncertain_condition_does_not_earn_comparison_points(self):
        p=self.product(condition=None);other=self.product(store="Jarir")
        offers=matching_offers(p,[other]);self.assertEqual(len(offers),1)
        self.assertEqual(evaluate(p,offers)["trust_components"]["cross_store"],0)

    def test_old_peer_not_compared(self):
        peer=self.product(store="Jarir",last_seen=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat())
        self.assertEqual(matching_offers(self.product(),[peer]),[])

    def test_jarir_catalog_uses_final_not_regular_price(self):
        payload={"data":{"hits":{"hits":[{"_source":{"sku":"123456","name":"Wireless Mouse","type_id":"simple","jarir_final_price":100,"regular_price":200,"url_path":"mouse-123456.html"}}]}}}
        p=parse_catalog(payload,{"123456"})[0]
        self.assertEqual(p["price"],100);self.assertEqual(p["original_price"],200)
        self.assertEqual(p["url"],"https://www.jarir.com/sa-en/mouse-123456.html")
        self.assertEqual(parse_catalog(payload,{"999999"}),[])
        payload["data"]["hits"]["hits"][0]["_source"]["type_id"]="configurable"
        self.assertEqual(parse_catalog(payload,{"123456"}),[])

    def test_public_search_denial_stops_before_catalog(self):
        html='<script id="__NUXT_DATA__" type="application/json">'+json.dumps([{"$ssiteConfig:searchProviderKeys":1},{"en":2},"public-index-test"])+"</script>"
        session=Mock();session.get.return_value.status_code=403
        with self.assertRaises(RuntimeError):discover(session,html,"mouse",{},20)
        self.assertEqual(session.get.call_count,1)

    def test_partial_search_results_not_discarded(self):
        html='<div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Wireless Mouse</a></h2><span class="a-price"><span class="a-offscreen">SAR 100</span></span></div>'
        response=Mock(status_code=200,text=html,content=html.encode(),url="https://www.amazon.sa/s?k=mouse")
        with patch.object(main.requests.Session,"get",return_value=response):
            self.assertEqual(len(main.fetch_store("Amazon.sa","mouse")),1)

    def test_amazon_tracking_cannot_duplicate_identity(self):
        from scrapers.common import canonical_url
        self.assertEqual(canonical_url("https://www.amazon.sa/name/dp/B000000001?dib=tracking&keywords=mouse&th=1"),"https://www.amazon.sa/dp/B000000001")

    def test_amazon_search_page_is_not_a_product(self):
        from scrapers.common import parse_jsonld_products
        html='<article class="product-tile"><a href="/s?k=mouse">Customers frequently viewed</a><span class="sale-price">SAR 100</span></article>'
        self.assertEqual(parse_jsonld_products(html,"https://www.amazon.sa/s?k=mouse","Amazon.sa"),[])

    def test_legacy_readings_remain_but_do_not_establish_verified_discount(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(database,"DB_PATH",str(Path(temp)/"db.sqlite")):
                item=dict(title="Wireless Mouse",store="Amazon.sa",url="https://www.amazon.sa/dp/B000000001",price=200)
                database.save_and_evaluate(item)
                db=database.connect()
                key=item["_product_key"]
                for i in range(10):
                    stamp=(datetime.now(timezone.utc)-timedelta(days=10-i)).strftime("%Y-%m-%d %H:%M:%S")
                    db.execute("INSERT INTO price_history(product_key,price,checked_at,validated) VALUES(?,?,?,0)",(key,200,stamp))
                db.commit();db.close()
                item["price"]=100;database.save_and_evaluate(item)
                self.assertNotEqual(item["status"],"VERIFIED_DEAL")
                self.assertEqual(database.get_archive_stats()["price_observations"],12)

    def test_snapshot_exposes_separate_claimed_savings(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(database,"DB_PATH",str(Path(temp)/"db.sqlite")):
                database.save_and_evaluate(dict(title="Wireless Mouse",store="Amazon.sa",url="https://www.amazon.sa/dp/B000000001",price=100,original_price=200))
                s=snapshot();e=s["products"][0]["evidence"]
                self.assertEqual(s["schema_version"],3)
                self.assertEqual(e["advertised_savings"]["amount_sar"],100)
                self.assertIsNone(e["historical_savings"]["amount_sar"])
                self.assertFalse(e["verified_deal"])


class ActiveFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.patch=patch.object(database,"DB_PATH",str(Path(self.temp.name)/"db.sqlite"));self.patch.start()

    def tearDown(self):
        self.patch.stop();self.temp.cleanup()

    def item(self,store="Amazon.sa",identifier="B000000001",price=100,original=200):
        url="https://www.amazon.sa/dp/"+identifier if store=="Amazon.sa" else "https://www.jarir.com/sa-en/product-"+identifier+".html" if store=="Jarir" else "https://www.noon.com/saudi-en/product/N123/p/"
        return dict(title="Reliable Test Product",store=store,url=url,price=price,original_price=original,product_id=identifier,data_sources=["fixture"])

    def seed_history(self,item,price=200,validated=1):
        database.save_and_evaluate(item)
        db=database.connect()
        # Move the first observation out of today's current-price slot.
        db.execute("UPDATE price_history SET checked_at=datetime('now','-12 days') WHERE product_key=?",(item["_product_key"],))
        for day in range(10,0,-1):
            stamp=(datetime.now(timezone.utc)-timedelta(days=day)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute("INSERT INTO price_history(product_key,price,checked_at,validated) VALUES(?,?,?,?)",(item["_product_key"],price,stamp,validated))
        db.commit();db.close()

    def test_retired_stores_are_absent_but_archive_preserved(self):
        database.save_and_evaluate(self.item())
        database.save_and_evaluate(self.item(store="Jarir",identifier="123456"))
        database.save_and_evaluate(self.item(store="Noon"))
        feed=snapshot()
        self.assertEqual(set(feed["active_stores"]),{"Amazon.sa","Jarir"})
        self.assertEqual({p["store"] for p in feed["products"]},{"Amazon.sa","Jarir"})
        self.assertEqual(feed["stats"]["archive_products"],3)
        self.assertEqual(feed["stats"]["products"],2)
        self.assertEqual(database.get_archive_stats()["products"],3)

    def test_advertised_25_is_featured_but_unverified(self):
        database.save_and_evaluate(self.item(price=150,original=200))
        p=snapshot()["products"][0]
        self.assertTrue(p["featured"]);self.assertEqual(p["deal_type"],"POTENTIAL")
        self.assertEqual(p["display_discount_source"],"ADVERTISED")
        self.assertEqual(p["display_discount_percent"],25)
        self.assertFalse(p["evidence"]["verified_deal"])
        self.assertFalse(p["history_summary"]["sufficient"])

    def test_under_25_not_featured(self):
        database.save_and_evaluate(self.item(price=151,original=200))
        self.assertFalse(snapshot()["products"][0]["featured"])

    def test_claimed_40_and_actual_10_reports_actual_when_trusted(self):
        item=self.item(price=200,original=None);self.seed_history(item)
        current=self.item(price=180,original=300);database.save_and_evaluate(current)
        p=snapshot()["products"][0]
        self.assertEqual(current["status"],"VERIFIED_DEAL")
        self.assertTrue(p["featured"]);self.assertEqual(p["deal_type"],"VERIFIED")
        self.assertEqual(p["display_discount_source"],"HISTORICAL")
        self.assertEqual(p["display_discount_percent"],10)
        self.assertEqual(p["evidence"]["advertised_savings"]["percent"],40)
        self.assertTrue(p["history_summary"]["sufficient"])

    def test_zero_drop_never_becomes_trusted(self):
        item=self.item(price=200,original=None);self.seed_history(item)
        self.assertEqual(database.historical_assessment([(200,(datetime.now(timezone.utc)-timedelta(days=d)).strftime("%Y-%m-%d")) for d in range(10,0,-1)],200)["status"],"PRICE_NORMAL")
        database.save_and_evaluate(self.item(price=200,original=400))
        p=snapshot()["products"][0]
        self.assertTrue(p["featured"])
        self.assertFalse(p["evidence"]["verified_deal"])

    def test_one_cent_actual_drop_with_sufficient_history_is_real(self):
        rows=[(200,(datetime.now(timezone.utc)-timedelta(days=d)).strftime("%Y-%m-%d")) for d in range(10,0,-1)]
        self.assertEqual(database.historical_assessment(rows,199.99)["status"],"VERIFIED_DEAL")
        self.assertEqual(database.historical_assessment(rows,200)["status"],"PRICE_NORMAL")

    def test_unvalidated_latest_price_cannot_verify(self):
        item=self.item(price=200,original=None);self.seed_history(item)
        db=database.connect();db.execute("INSERT INTO price_history(product_key,price,validated) VALUES(?,?,0)",(item["_product_key"],100));db.commit();db.close()
        p=snapshot()["products"][0]
        self.assertFalse(p["latest_validated"])
        self.assertFalse(p["evidence"]["verified_deal"])
        self.assertFalse(p["featured"])

    def test_same_price_unvalidated_latest_row_is_not_promoted_by_metadata(self):
        item=self.item(price=100,original=200);database.save_and_evaluate(item)
        db=database.connect();db.execute("INSERT INTO price_history(product_key,price,validated) VALUES(?,?,0)",(item["_product_key"],100));db.commit();db.close()
        p=snapshot()["products"][0]
        self.assertFalse(p["latest_validated"])
        self.assertFalse(p["featured"])

    def test_same_asin_duplicates_are_one_listing_and_rows_remain(self):
        item=self.item();database.save_and_evaluate(item)
        db=database.connect()
        db.execute("INSERT INTO products(product_key,store,title,url) VALUES(?,?,?,?)",("old-key","Amazon.sa","Reliable Older Product","https://www.amazon.sa/name/dp/B000000001?tag=old"))
        db.execute("INSERT INTO price_history(product_key,price,validated) VALUES(?,?,0)",("old-key",100))
        db.commit();db.close()
        feed=snapshot()
        self.assertEqual(len(feed["products"]),1)
        self.assertEqual(feed["stats"]["archive_products"],2)
        self.assertEqual(feed["stats"]["hidden_legacy_products"],1)

    def test_unchanged_price_new_original_claim_updates_offer_without_history_rewrite(self):
        item=self.item(original=None);database.save_and_evaluate(item)
        database.save_and_evaluate(self.item(original=200))
        p=snapshot()["products"][0]
        self.assertEqual(p["store_original_price"],200)
        self.assertTrue(p["featured"])
        self.assertEqual(database.get_archive_stats()["price_observations"],1)
        db=database.connect();self.assertIsNone(db.execute("SELECT store_original_price FROM price_history").fetchone()[0]);db.close()

    def test_only_real_safe_images_are_exposed(self):
        item=self.item();item["image_url"]="https://m.media-amazon.com/images/I/real-source.jpg";database.save_and_evaluate(item)
        self.assertEqual(snapshot()["products"][0]["image_url"],item["image_url"])
        item["image_url"]="https://localhost/private";database.save_and_evaluate(item)
        self.assertIsNone(snapshot()["products"][0]["image_url"])

    def test_history_chart_exposes_validation_flags(self):
        database.save_and_evaluate(self.item())
        history=snapshot()["products"][0]["history"]
        self.assertTrue(all(isinstance(point["validated"],bool) for point in history))


if __name__=="__main__":unittest.main()

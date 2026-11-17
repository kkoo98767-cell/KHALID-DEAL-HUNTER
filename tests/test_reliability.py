import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock
import requests
import database
import ai_analyzer as ai
import main
from telegram_bot import format_deal, _send_text
from scrapers.common import parse_jsonld_products, _number, canonical_url


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = database.DB_PATH
        database.DB_PATH = str(Path(self.temp.name)/"db.sqlite")
        ai._PROVIDER_COOLDOWN.clear()
        ai._REQUESTS = 0

    def tearDown(self):
        database.DB_PATH = self.old
        self.temp.cleanup()

    def item(self, price=100):
        return dict(title="منتج تجريبي",store="Noon",url="https://www.noon.com/saudi-en/test/N123/p/",price=price)

    def history(self, prices):
        today = datetime.now(timezone.utc)
        return [(p,(today-timedelta(days=len(prices)-i)).strftime("%Y-%m-%d %H:%M:%S")) for i,p in enumerate(prices)]

    def test_four_store_fixtures(self):
        for store,name,host in [("Amazon.sa","amazon","www.amazon.sa"),("Noon","noon","www.noon.com"),("Extra","extra","www.extra.com"),("Jarir","jarir","www.jarir.com")]:
            with self.subTest(store=store):
                items=parse_jsonld_products((Path(__file__).parent/"fixtures"/(name+".html")).read_text(encoding="utf-8"),"https://"+host+"/search",store)
                self.assertEqual(len(items),1)
                self.assertGreater(items[0]["price"],0)

    def test_verified_threshold_and_store_claim(self):
        h=self.history([200]*9)
        self.assertEqual(database.historical_assessment(h,150)["status"],"VERIFIED_DEAL")
        self.assertEqual(database.historical_assessment(h,151)["status"],"VERIFIED_DEAL")
        self.assertEqual(database.historical_assessment(h,180)["drop_percent"],10)

    def test_spike_cannot_create_discount(self):
        self.assertEqual(database.historical_assessment(self.history([200]*9+[1000]),180)["drop_percent"],10)

    def test_intraday_samples_insufficient(self):
        self.assertEqual(database.historical_assessment([(200,"2026-11-16 01:00:00")]*50,100)["status"],"INSUFFICIENT_HISTORY")

    def test_price_increase_and_anomaly(self):
        h=self.history([200]*9)
        self.assertEqual(database.historical_assessment(h,250)["status"],"PRICE_INCREASED")
        self.assertEqual(database.historical_assessment(h,1)["status"],"DATA_ANOMALY")

    def test_potential_is_never_verified(self):
        item=self.item();item["original_price"]=200
        database.save_and_evaluate(item)
        self.assertEqual(item["status"],"POTENTIAL_DEAL")
        self.assertNotIn("✅",format_deal(item))

    def test_invalid_price_does_not_archive(self):
        for price in [float("nan"),float("inf"),0,-1,True]:
            with self.assertRaises(ValueError):database.save_and_evaluate(self.item(price))
        self.assertEqual(database.get_archive_stats()["products"],0)

    def test_alert_changes_and_history(self):
        item=self.item();item["status"]="POTENTIAL_DEAL"
        database.record_alert(item)
        self.assertTrue(database.was_recently_alerted(item))
        item["status"]="VERIFIED_DEAL"
        self.assertFalse(database.was_recently_alerted(item))
        database.record_alert(item)
        item["price"]=90
        self.assertFalse(database.was_recently_alerted(item))
        db=database.connect();self.assertEqual(db.execute("SELECT COUNT(*) FROM alert_history").fetchone()[0],2);db.close()

    def test_migration_preserves_original_rows(self):
        db=database.connect();db.close()
        item=self.item();database.save_and_evaluate(item)
        for _ in range(3):
            db=database.connect();db.close()
        self.assertEqual(database.get_archive_stats()["price_observations"],1)

    def test_arabic_persian_and_thousands(self):
        for text in ["١٬٢٩٩٫٩٠ ر.س","۱٬۲۹۹٫۹۰","SAR 1,299.90"]:self.assertEqual(_number(text),1299.9)

    def test_invalid_external_links_and_missing_prices(self):
        for offers in [{"price":99,"url":"https://evil.example/p/x"},{"url":"/p/x"}]:
            html='<script type="application/ld+json">'+json.dumps(dict(name="Mouse",offers=offers,**{"@type":"Product"}))+'</script>'
            self.assertEqual(parse_jsonld_products(html,"https://www.noon.com/search","Noon"),[])

    def test_amazon_canonical_identity(self):
        self.assertEqual(canonical_url("https://www.amazon.sa/name/dp/B000000001?tag=abc#x"),"https://www.amazon.sa/dp/B000000001")

    def test_ai_fallback_and_cache(self):
        good=dict(provider="Groq",worth_alerting=True,reason="بيانات محدودة",telegram_message="المواصفات غير متاحة؛ راجع المتجر.")
        with patch.dict("os.environ",{"GEMINI_API_KEY":"test","GROQ_API_KEY":"test"}), patch.object(ai,"_analyze_with_gemini",side_effect=requests.Timeout), patch.object(ai,"_analyze_with_groq",return_value=good) as groq:
            self.assertEqual(ai.analyze_deal(self.item())["provider"],"Groq")
            ai.analyze_deal(self.item());self.assertEqual(groq.call_count,1)

    def test_ai_total_outage(self):
        with patch.dict("os.environ",{"GEMINI_API_KEY":"","GROQ_API_KEY":""}):
            self.assertEqual(ai.analyze_deal(self.item())["provider"],"Fallback")

    def test_ai_rejects_factual_overrides(self):
        for text in ["Verified deal", "عرض موثوق", "السعر 100 ريال", "✅ ممتاز"]:
            with self.assertRaises(ValueError):ai._validate_result(json.dumps(dict(worth_alerting=True,reason="سبب عربي",telegram_message=text)),"test")

    def test_old_schema_migrates_without_deleting_history(self):
        db=sqlite3.connect(database.DB_PATH)
        db.executescript("CREATE TABLE products(product_key TEXT PRIMARY KEY,store TEXT NOT NULL,title TEXT NOT NULL,url TEXT NOT NULL,first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP); CREATE TABLE price_history(id INTEGER PRIMARY KEY AUTOINCREMENT,product_key TEXT NOT NULL,price REAL NOT NULL,checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP); INSERT INTO products(product_key,store,title,url) VALUES('key','Noon','Original','https://www.noon.com/p/original'); INSERT INTO price_history(product_key,price) VALUES('key',123);")
        db.commit();db.close()
        db=database.connect()
        self.assertEqual(db.execute("SELECT product_key,price FROM price_history").fetchall(),[("key",123)])
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0],"ok")
        db.close()

    def test_api_read_and_token_and_cors(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from api import Handler
        database.save_and_evaluate(dict(title="Wireless Mouse",store="Amazon.sa",url="https://www.amazon.sa/dp/B000000001",price=100))
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            r=requests.get(base+"/api/dashboard",timeout=3)
            self.assertEqual(r.status_code,200)
            self.assertEqual(r.json()["stats"]["products"],1)
            self.assertEqual(requests.get(base+"/",timeout=3).status_code,200)
            with patch.dict("os.environ",{"API_TOKEN":"private-test","DASHBOARD_ORIGIN":"https://allowed.example"}):
                self.assertEqual(requests.get(base+"/api/dashboard",timeout=3).status_code,401)
                r=requests.get(base+"/api/dashboard",headers={"Authorization":"Bearer private-test","Origin":"https://allowed.example"},timeout=3)
                self.assertEqual(r.status_code,200)
                self.assertEqual(r.headers.get("Access-Control-Allow-Origin"),"https://allowed.example")
                self.assertNotIn("private-test",r.text)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_conflicting_prices_are_rejected(self):
        html='<script type="application/ld+json">{"@type":"Product","name":"Test Mouse","url":"/dp/B000000001","offers":{"price":100}}</script><div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Test Mouse</a></h2><span class="a-price"><span class="a-offscreen">SAR 200</span></span></div>'
        self.assertEqual(parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa"),[])

    def test_discovery_rotates_without_github_run_number(self):
        from discovery import choose_search
        a=choose_search("Jarir")[1];b=choose_search("Jarir")[1]
        self.assertNotEqual(a,b)

    def test_partial_scan_keeps_other_store_prices(self):
        def fetch(store,query):
            if store == "Amazon.sa":raise requests.Timeout()
            return [dict(title="Valid Product",store=store,url="https://"+store.lower()+".example/p/one",price=100)]
        fallback=dict(provider="Fallback",worth_alerting=True,reason="تعذر التحليل",telegram_message="التقييم غير متاح")
        with patch.object(main,"fetch_store",side_effect=fetch),patch.object(main.time,"sleep"),patch.object(main,"refresh_tracked",return_value=[]),patch.object(main,"analyze_deal",return_value=fallback),patch.object(main,"send_startup_status",side_effect=RuntimeError),patch.object(main,"send_scan_summary") as summary,patch("dashboard.export"),patch.object(main,"Path"),patch("ai_analyzer.evaluate_archive_backlog",return_value=set()):
            with self.assertRaises(RuntimeError):main.main()
            self.assertEqual(database.get_archive_stats()["products"],1)
            self.assertEqual(summary.call_args.kwargs["products_seen"],1)
            self.assertEqual(summary.call_args.kwargs["failures"],1)

    def test_stale_history_is_not_verified(self):
        stale=[(200,(datetime.now(timezone.utc)-timedelta(days=30+i)).strftime("%Y-%m-%d")) for i in range(10)]
        self.assertEqual(database.historical_assessment(stale,100)["status"],"INSUFFICIENT_HISTORY")

    def test_same_product_different_names_do_not_merge(self):
        first=self.item();second=self.item();second["url"]="https://www.noon.com/saudi-en/test/N456/p/"
        database.save_and_evaluate(first);database.save_and_evaluate(second)
        self.assertEqual(database.get_archive_stats()["products"],2)

    def test_missing_metadata_does_not_erase_brand(self):
        item=self.item();item["brand"]="Example"
        database.save_and_evaluate(item);database.save_and_evaluate(self.item())
        db=database.connect();self.assertEqual(db.execute("SELECT brand FROM products").fetchone()[0],"Example");db.close()

    def test_negative_and_infinite_numeric_parsing(self):
        self.assertIsNone(_number("-10 SAR"));self.assertIsNone(_number(float("inf")))

    def test_price_text_is_not_product_identity(self):
        from scrapers.common import reliable_title
        for title in ["SAR 99.99", "الشراء لمرة واحدة", "الخيارات: 3 مقاسات", "‏19.02 ريال ريال ‎ 19 . ‎ 02 ‏"]:
            self.assertFalse(reliable_title(title))

    def test_scan_id_prevents_duplicate_observation(self):
        first=self.item();first["_scan_id"]="scan-one"
        second=self.item(90);second["_scan_id"]="scan-one"
        database.save_and_evaluate(first);database.save_and_evaluate(second)
        self.assertFalse(second["_price_recorded"])
        self.assertEqual(database.get_archive_stats()["price_observations"],1)
        second["_scan_id"]="scan-two";database.save_and_evaluate(second)
        self.assertEqual(database.get_archive_stats()["price_observations"],2)

    def test_structured_metadata_is_preserved(self):
        first=self.item();first["description"]="Source description";first["gtin"]="1234567890123"
        database.save_and_evaluate(first);database.save_and_evaluate(self.item())
        db=database.connect();metadata,gtin=db.execute("SELECT metadata,gtin FROM products").fetchone();db.close()
        self.assertEqual(json.loads(metadata)["description"],"Source description")
        self.assertEqual(gtin,"1234567890123")

    def test_advertised_price_is_only_potential_signal(self):
        html='<div data-component-type="s-search-result"><h2><a href="/dp/B000000001">Wireless Mouse</a></h2><span class="a-price"><span class="a-offscreen">SAR 100</span></span><span class="a-text-price"><span class="a-offscreen">SAR 200</span></span></div>'
        item=parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa")[0]
        self.assertEqual(item["original_price"],200)
        database.save_and_evaluate(item)
        self.assertEqual(item["status"],"POTENTIAL_DEAL")
        self.assertIsNone(item["baseline"])

    def test_many_links_to_same_product_are_one_identity(self):
        links=''.join('<a href="/dp/B000000001?ref='+str(i)+'">Details</a>' for i in range(12))
        html='<div data-component-type="s-search-result"><a href="/dp/B000000001"><h2>Wireless Headphones</h2></a>'+links+'<span class="a-price"><span class="a-offscreen">SAR 100</span></span></div>'
        items=parse_jsonld_products(html,"https://www.amazon.sa/s","Amazon.sa")
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]["title"],"Wireless Headphones")

    def test_timeout_bounded(self):
        with patch.object(requests.Session,"get",side_effect=requests.Timeout) as get,patch.object(main.time,"sleep"):
            with self.assertRaises(requests.Timeout):main.fetch_store("Amazon.sa","mouse")
            self.assertEqual(get.call_count,3)

    def test_access_denial_stops(self):
        with patch.object(requests.Session,"get",return_value=Mock(status_code=403)) as get:
            with self.assertRaises(RuntimeError):main.fetch_store("Amazon.sa","mouse")
            self.assertEqual(get.call_count,1)

    def test_telegram_rejection_not_recorded(self):
        with patch("telegram_bot.TG_TOKEN","test"),patch("telegram_bot.TG_CHAT","test"),patch("telegram_bot.requests.post",return_value=Mock(ok=True,json=lambda:{"ok":False})):
            with self.assertRaises(RuntimeError):_send_text("اختبار")


if __name__ == "__main__":unittest.main()

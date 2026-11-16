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
        self.assertEqual(database.historical_assessment(h,151)["status"],"PRICE_NORMAL")
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
        database.save_and_evaluate(self.item())
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

    def test_timeout_bounded(self):
        with patch.object(requests.Session,"get",side_effect=requests.Timeout) as get,patch.object(main.time,"sleep"):
            with self.assertRaises(requests.Timeout):main.fetch_store("Noon","mouse")
            self.assertEqual(get.call_count,2)

    def test_access_denial_stops(self):
        with patch.object(requests.Session,"get",return_value=Mock(status_code=403)) as get:
            with self.assertRaises(RuntimeError):main.fetch_store("Noon","mouse")
            self.assertEqual(get.call_count,1)

    def test_telegram_rejection_not_recorded(self):
        with patch("telegram_bot.TG_TOKEN","test"),patch("telegram_bot.TG_CHAT","test"),patch("telegram_bot.requests.post",return_value=Mock(ok=True,json=lambda:{"ok":False})):
            with self.assertRaises(RuntimeError):_send_text("اختبار")


if __name__ == "__main__":unittest.main()

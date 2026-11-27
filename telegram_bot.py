from urllib.parse import urlencode
import random
import time
import requests

from config import TG_TOKEN, TG_CHAT, STORES

OPENINGS = [
    "👀 لفت انتباهي هذا المنتج",
    "🔎 من نتائج البحث اليوم",
    "🛍️ شوف هذا اللي طلع معنا",
    "💡 هذا المنتج يستحق نظرة",
    "🎯 عندنا مرشح جديد",
]


def _send_text(message, timeout=20):
    if not TG_TOKEN or not TG_CHAT:
        raise RuntimeError("Telegram secrets are missing: TG_TOKEN or TG_CHAT.")
    for attempt in range(2):
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": message, "disable_web_page_preview": False},
            timeout=timeout,
        )
        if response.status_code != 429 or attempt:
            break
        try:
            delay = response.json()["parameters"]["retry_after"]
        except (ValueError, KeyError, TypeError):
            break
        if type(delay) is not int or not 0 <= delay <= 60:
            break
        time.sleep(delay)
    if not response.ok:
        raise RuntimeError(f"Telegram HTTP {response.status_code}")
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("Telegram returned a non-JSON response.") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected the message: {result.get('description', 'unknown error')}")
    return result


def send_startup_status(run_number=None, run_id=None, mode="hunt"):
    """Send a clear heartbeat as soon as the main workflow starts."""
    details = []
    if run_number:
        details.append(f"رقم التشغيل: #{run_number}")
    if run_id:
        details.append(f"معرّف التشغيل: {run_id}")
    suffix = "\n" + "\n".join(details) if details else ""

    if mode == "ai_test":
        message = (
            "🧪 بدأ اختبار اتصال الذكاء الاصطناعي في KHALID DEAL HUNTER.\n"
            "سيتم اختبار المزوّد الذي اخترته فقط، دون فحص المتاجر أو تعديل سجل الأسعار."
            + suffix
        )
    else:
        message = (
            "🟢 KHALID DEAL HUNTER بدأ التشغيل بنجاح.\n"
            "تم الاتصال بتيليجرام، والبوت الآن يفحص المتاجر ويبحث عن المنتجات والعروض.\n"
            "هذه رسالة تأكيد تشغيل وليست تنبيهاً عن عرض."
            + suffix
        )
    return _send_text(message)


def send_ai_test_status(provider, success, detail="", run_number=None):
    """Report the result of a manually requested provider test to Telegram."""
    run_text = f"\nرقم التشغيل: #{run_number}" if run_number else ""
    if success:
        message = (
            f"🟢 اختبار {provider} ناجح.\n"
            "تم الاتصال بالمزوّد واستلام رد فعلي من النموذج بنجاح.\n"
            f"التفاصيل: {detail or 'النموذج رد بنجاح.'}"
            f"{run_text}"
        )
    else:
        message = (
            f"🔴 اختبار {provider} فشل.\n"
            "لم نتمكن من إكمال الاتصال واستلام رد صالح من النموذج.\n"
            f"السبب: {'راجع سجل التشغيل في GitHub Actions للتفاصيل الفنية.'}"
            f"{run_text}"
        )
    return _send_text(message)


def format_deal(item):
    labels = {"VERIFIED_DEAL": "✅ عرض موثوق — تم التحقق من قاعدة البيانات", "POTENTIAL_DEAL": "⚠️ انخفاض محتمل غير مؤكد؛ التاريخ غير كافٍ", "NEW_PRODUCT": "🆕 منتج جديد؛ بدأ تسجيل تاريخه السعري", "PRICE_INCREASED": "📈 ارتفع السعر ولم يعد يحقق شروط العرض", "PRICE_NORMAL": "📊 السعر ضمن النطاق المعتاد", "INSUFFICIENT_HISTORY": "⚠️ التاريخ السعري غير كافٍ", "DATA_ANOMALY": "⚠️ قراءة سعر غير اعتيادية تحتاج مراجعة"}
    status = labels.get(item.get("status"), labels["INSUFFICIENT_HISTORY"])
    store = {"Amazon.sa":"أمازون السعودية", "Noon":"نون", "Extra":"إكسترا", "Jarir":"جرير"}.get(item["store"], item["store"])
    message = f"{status}\n\n📦 المنتج: {item['title']}\n🏬 المتجر: {store}\n💰 السعر الحالي: {item['price']:.2f} ريال"
    if item.get("baseline") is not None:
        message += f"\n📊 خط الأساس التاريخي: {item['baseline']:.2f} ريال\n📉 الانخفاض الفعلي: {item['drop_percent']}٪"
    if item.get("last_alert_price", 0) > item["price"]:
        message += f"\nنزل {item['last_alert_price'] - item['price']:.2f} ريال عن آخر تنبيه"
    comment = item.get("ai_message") or "التقييم الآلي غير متاح. معلومات الجودة والمواصفات غير كافية للتوصية بالشراء."
    message += f"\n\n🤖 التقييم: {comment}\n\n🔗 رابط المنتج: {item['url']}"
    detail = urlencode({"product": item.get("_product_key") or item["url"]})
    message += "\nسجل السعر: https://khalid-deal-hunter-monitor.tealbasket5.chatgpt.site/?" + detail
    return message


def send_deal(item):
    return _send_text(format_deal(item))


def send_deal_pair(amazon, jarir):
    return _send_text(format_deal(amazon) + "\n\n━━━━━━━━━━\n\n" + format_deal(jarir))


def send_scan_summary(run_number=None, products_seen=0, new_products=0,
                      failures=0, empty_stores=0, archive_stats=None, **metrics):
    """Send an end-of-run archive report so a successful workflow cannot hide an empty scan."""
    stats = archive_stats or {}
    run_text = f"\nرقم التشغيل: #{run_number}" if run_number else ""
    coverage = {"COMPLETE": "مكتمل", "PARTIAL": "جزئي؛ تعذر إكمال بعض المتاجر", "UNAVAILABLE": "تعذر جمع أسعار جديدة"}.get(metrics.get("coverage_status"), "راجع تفاصيل المتاجر")
    message = (
        "📊 تقرير أرشيف KHALID DEAL HUNTER\n\n"
        f"حالة الفحص: {coverage}\n"
        f"🧾 منتجات تم فحصها في هذا التشغيل: {products_seen}\n"
        f"🆕 منتجات جديدة أضيفت للأرشيف: {new_products}\n"
        f"📚 إجمالي المنتجات المؤرشفة: {stats.get('products', 0)}\n"
        f"📈 إجمالي قراءات الأسعار: {stats.get('price_observations', 0)}\n"
        f"🏬 المتاجر النشطة في المراقبة: {len(STORES)}\n"
        f"⚠️ متاجر بلا نتائج: {empty_stores}\n"
        f"❌ متاجر/عمليات واجهت أخطاء: {failures}\n"
        f"متاجر تم فحصها: {metrics.get('stores_checked', len(STORES))}\n"
        f"متاجر نجح استخراجها: {metrics.get('successful_stores', 0)}\n"
        f"منتجات لها تاريخ كافٍ: {metrics.get('sufficient_history', 0)}\n"
        f"عروض محتملة: {metrics.get('potential_deals', 0)}\n"
        f"عروض موثوقة: {metrics.get('verified_deals', 0)}\n"
        f"عروض غير مؤكدة: {metrics.get('potential_deals', 0)}\n"
        f"مزود التحليل: {metrics.get('providers', 'غير متاح')}"
        f"{run_text}"
    )
    if "pairs_sent" in metrics:
        message += f"\nرسائل العروض المشتركة: {metrics['pairs_sent']} (منتج من كل متجر في الرسالة)"
        for store, count in metrics["eligible_by_store"].items():
            message += f"\n{store}: {count} منتجات مؤهلة للتنبيه"
    for store, report in metrics.get("store_reports", {}).items():
        if report.get("error") or not report.get("observed_products"):
            reason = ("المتجر قيّد الوصول" if report.get("blocked") else
                      "ما رجعت نتائج قابلة للرصد" if report.get("error") in (None, "EmptyStoreResults") else
                      "تعذرت قراءة المتجر (" + str(report["error"]) + ")")
            message += f"\n{store}: {reason}"
    if metrics.get("comment"):
        message += "\n\n" + metrics["comment"]
    return _send_text(message)

import random
import requests

from config import TG_TOKEN, TG_CHAT

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
    response = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": message, "disable_web_page_preview": False},
        timeout=timeout,
    )
    response.raise_for_status()
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
            f"السبب: {detail or 'راجع سجل التشغيل في GitHub Actions.'}"
            f"{run_text}"
        )
    return _send_text(message)


def send_deal(item):
    opening = random.choice(OPENINGS)
    ai_message = item.get("ai_message", "").strip()
    if not ai_message:
        ai_message = "اجتاز المنتج التقييم، لكن ما توفر تعليق إضافي."

    rating = item.get("rating", 0)
    reviews = item.get("reviews", 0)
    rating_text = f"{rating:g}/5" if rating else "غير متوفر"
    reviews_text = f"{reviews:,}" if reviews else "غير متوفر"

    optional_details = []
    purchase_text = item.get("purchase_count_text", "").strip()
    if purchase_text:
        optional_details.append(f"🛒 مؤشر الشراء الظاهر بالمتجر: {purchase_text}")
    snippets = item.get("review_snippets", []) or []
    if snippets:
        optional_details.append("💬 مقتطفات من آراء العملاء:")
        optional_details.extend(f"• {snippet[:300]}" for snippet in snippets[:3])
    optional_section = "\n\n" + "\n".join(optional_details) if optional_details else ""

    message = (
        f"{opening}\n\n"
        f"🛍️ {item['title']}\n"
        f"🏬 المتجر: {item['store']}\n\n"
        f"💰 السعر الحالي: {item['price']:.2f} ريال\n"
        f"📊 متوسط الأسعار السابقة: {item['baseline']:.2f} ريال\n"
        f"📉 الانخفاض عن المتوسط: {item['drop_percent']}%\n"
        f"⭐ تقييم المنتج: {rating_text}\n"
        f"📝 عدد المراجعات: {reviews_text}"
        f"{optional_section}\n\n"
        f"🤖 رأي KHALID DEAL HUNTER:\n{ai_message}\n\n"
        f"🔗 رابط المنتج:\n{item['url']}\n\n"
        "تنبيه: المقارنة مبنية على سجل الأسعار الذي جمعه البوت، "
        "وليست ضماناً لاستمرار السعر أو توفر المنتج."
    )
    return _send_text(message)


def send_scan_summary(run_number=None, products_seen=0, new_products=0,
                      failures=0, empty_stores=0, archive_stats=None):
    """Send an end-of-run archive report so a successful workflow cannot hide an empty scan."""
    stats = archive_stats or {}
    run_text = f"\\nرقم التشغيل: #{run_number}" if run_number else ""
    message = (
        "📊 تقرير أرشيف KHALID DEAL HUNTER\\n\\n"
        f"🧾 منتجات تم فحصها في هذا التشغيل: {products_seen}\\n"
        f"🆕 منتجات جديدة أضيفت للأرشيف: {new_products}\\n"
        f"📚 إجمالي المنتجات المؤرشفة: {stats.get('products', 0)}\\n"
        f"📈 إجمالي قراءات الأسعار: {stats.get('price_observations', 0)}\\n"
        f"🏬 متاجر لها منتجات مؤرشفة: {stats.get('stores_with_products', 0)} من 4\\n"
        f"⚠️ متاجر بلا نتائج: {empty_stores}\\n"
        f"❌ متاجر/عمليات واجهت أخطاء: {failures}"
        f"{run_text}"
    )
    return _send_text(message)

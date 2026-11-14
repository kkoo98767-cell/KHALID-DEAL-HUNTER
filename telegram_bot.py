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


def send_deal(item):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram secrets are missing.")
        return

    opening = random.choice(OPENINGS)
    ai_message = item.get("ai_message", "").strip()
    if not ai_message:
        ai_message = "اجتاز المنتج التقييم، لكن ما توفر تعليق إضافي."

    rating = item.get("rating", 0)
    reviews = item.get("reviews", 0)
    rating_text = f"{rating:g}/5" if rating else "غير متوفر"
    reviews_text = f"{reviews:,}" if reviews else "غير متوفر"

    text = (
        f"{opening}\n\n"
        f"🛍️ {item['title']}\n"
        f"🏬 المتجر: {item['store']}\n\n"
        f"💰 السعر الحالي: {item['price']:.2f} ريال\n"
        f"📊 متوسط الأسعار السابقة: {item['baseline']:.2f} ريال\n"
        f"📉 الانخفاض عن المتوسط: {item['drop_percent']}%\n"
        f"⭐ تقييم المنتج: {rating_text}\n"
        f"📝 عدد المراجعات: {reviews_text}\n\n"
        f"🤖 رأي KHALID DEAL HUNTER:\n{ai_message}\n\n"
        f"🔗 رابط المنتج:\n{item['url']}\n\n"
        "تنبيه: المقارنة مبنية على سجل الأسعار الذي جمعه البوت، "
        "وليست ضماناً لاستمرار السعر أو توفر المنتج."
    )

    response = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={
            "chat_id": TG_CHAT,
            "text": text,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    response.raise_for_status()

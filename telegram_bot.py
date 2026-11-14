
import random
import requests
from config import TG_TOKEN, TG_CHAT

OPENINGS = [
    "👀 خالد، شوف وش لقيت لك!",
    "🎯 اصطياد جديد من KHALID DEAL HUNTER",
    "🛍️ مرّ علي هذا العرض وحبيت أشاركك إياه",
    "💰 عندنا انخفاض سعر يستحق الانتباه",
    "اكتشاف جديد من جولة البحث اليوم 🔎",
]

CLOSINGS = [
    "إذا تحتاجه، يستحق تلقي نظرة عليه.",
    "يمكن يكون هذا الوقت المناسب لشرائه.",
    "خلّه في بالك إذا كنت تبحث عن مثله.",
    "السعر يستحق المقارنة قبل اتخاذ قرار الشراء.",
    "نواصل البحث عن عروض أفضل 👌",
]

def send_deal(item):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram secrets are missing.")
        return

    opening = random.choice(OPENINGS)
    closing = random.choice(CLOSINGS)

    text = (
        f"{opening}\n\n"
        f"🛍️ {item['title']}\n"
        f"🏬 المتجر: {item['store']}\n\n"
        f"💰 السعر الحالي: {item['price']:.2f} ريال\n"
        f"📊 متوسط الأسعار السابقة: {item['baseline']:.2f} ريال\n"
        f"📉 الانخفاض المسجل: {item['drop_percent']}%\n"
        f"⭐ التقييم: {item.get('rating', 0)}\n"
        f"📝 عدد المراجعات: {item.get('reviews', 0)}\n\n"
        f"🔗 رابط المنتج:\n{item['url']}\n\n"
        f"{closing}\n\n"
        "ملاحظة: المقارنة مبنية على سجل الأسعار الذي جمعه البوت."
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

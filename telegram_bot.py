
import requests
from config import TG_TOKEN, TG_CHAT

def send_deal(item):
    if not TG_TOKEN or not TG_CHAT:
        print("Telegram secrets are missing; skipping notification.")
        return

    text = (
        "🔥 فرصة سعرية محتملة\\n\\n"
        f"🛍️ {item['title']}\\n"
        f"🏬 المتجر: {item['store']}\\n"
        f"💰 السعر الحالي: {item['price']:.2f} ر.س\\n"
        f"📉 أقل من متوسط آخر الأسعار المسجلة بـ {item['drop_percent']}%\\n"
        f"📊 متوسط السعر السابق: {item['baseline']:.2f} ر.س\\n"
        f"⭐ التقييم: {item.get('rating', 0)} ({item.get('reviews', 0)} مراجعة)\\n\\n"
        f"🔗 {item['url']}\\n\\n"
        "ملاحظة: النسبة محسوبة من سجل البوت، وليست ضماناً للسعر الأدنى التاريخي."
    )
    response = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": False},
        timeout=20,
    )
    response.raise_for_status()

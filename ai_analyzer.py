import json
import os
import requests

from config import GEMINI_MODEL, REQUEST_TIMEOUT

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def analyze_deal(item):
    """Ask Gemini to judge a Python-verified price drop. Never overrides price rules."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    prompt = f"""
أنت محلل عروض تسوق سعودي. قيّم هل يستحق هذا المنتج تنبيه المستخدم.
لا تفترض أي معلومات غير موجودة، ولا تعتبر نسبة الخصم وحدها دليلاً قاطعاً.
السعر الحالي والمتوسط التاريخي محسوبان مسبقاً بواسطة البرنامج، ويجب أن يكون رأيك مساعداً فقط.
انتبه إلى أن المنتج قد يكون غير واضح أو عنوانه مضللاً أو المقارنة غير منطقية.
أعد JSON فقط بالشكل التالي:
{{"worth_alerting": true, "reason": "سبب عربي مختصر لا يتجاوز 18 كلمة"}}

بيانات المنتج:
الاسم: {item.get("title", "")}
المتجر: {item.get("store", "")}
السعر الحالي بالريال: {item.get("price", "")}
متوسط الأسعار السابقة بالريال: {item.get("baseline", "")}
الانخفاض المحسوب: {item.get("drop_percent", "")}%
التقييم: {item.get("rating", 0)}
عدد المراجعات: {item.get("reviews", 0)}

لا تزعم أنك تحققت من المخزون أو السعر الحالي على الموقع. إذا كانت البيانات غير كافية أو مشكوكاً فيها، اجعل worth_alerting=false.
"""

    response = requests.post(
        API_URL.format(model=GEMINI_MODEL),
        headers={
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
                "maxOutputTokens": 120,
            },
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()

    candidates = payload.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidate response")

    parts = candidates[0].get("content", {}).get("parts", [])
    answer = next((part.get("text", "") for part in parts if part.get("text")), "")
    if not answer:
        raise RuntimeError("Gemini returned an empty response")

    result = json.loads(answer)
    if not isinstance(result.get("worth_alerting"), bool):
        raise ValueError("Gemini response is missing a valid worth_alerting field")

    reason = str(result.get("reason", "")).strip()
    return {
        "worth_alerting": result["worth_alerting"],
        "reason": reason[:240],
    }

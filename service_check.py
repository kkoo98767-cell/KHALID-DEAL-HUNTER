"""Real credential tests in Actions; no keys, raw responses or request URLs logged."""
import json
import os
from pathlib import Path
import requests
from ai_analyzer import _analyze_with_gemini, _analyze_with_groq, _build_prompt
from telegram_bot import _send_text


def main():
    prompt = _build_prompt({"title":"فأرة لاسلكية", "store":"Amazon.sa", "price":100, "status":"INSUFFICIENT_HISTORY", "rating":None, "reviews":None})
    report = {}
    for provider,fn,keyname in [("Gemini",_analyze_with_gemini,"GEMINI_API_KEY"),("Groq",_analyze_with_groq,"GROQ_API_KEY")]:
        try:
            key=os.getenv(keyname)
            if not key:
                raise ValueError("Missing credential")
            result=fn(prompt,key)
            report[provider]={"ok":True,"provider":result["provider"],"arabic_comment":result["telegram_message"]}
        except Exception as exc:
            code=exc.response.status_code if isinstance(exc,requests.HTTPError) and exc.response is not None else None
            report[provider]={"ok":False,"error":type(exc).__name__,"http_status":code,"validation":str(exc) if isinstance(exc,ValueError) else None}
    try:
        receipt=_send_text("🧪 تم تنفيذ اختبار خدمات مراقبة الأسعار. نتيجة التحليل الآلي محفوظة في سجل التشغيل، وهذا الاختبار يؤكد قبول تيليجرام للرسالة.")
        report["Telegram"]={"ok":True,"message_id":receipt.get("result",{}).get("message_id")}
    except Exception as exc:
        report["Telegram"]={"ok":False,"error":type(exc).__name__}
    Path("service-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not all(r["ok"] for r in report.values()):
        raise SystemExit(1)


if __name__ == "__main__":main()

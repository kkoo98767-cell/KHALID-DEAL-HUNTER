import os
import sys

from ai_analyzer import test_ai_provider
from telegram_bot import send_startup_status, send_ai_test_status


def main():
    provider = os.getenv("AI_PROVIDER_TEST", "").strip().lower()
    if provider not in {"gemini", "groq"}:
        raise SystemExit("AI_PROVIDER_TEST must be either gemini or groq.")

    provider_name = "Gemini" if provider == "gemini" else "Groq"
    run_number = os.getenv("GITHUB_RUN_NUMBER")

    # Verify Telegram first so the result can be delivered.
    send_startup_status(
        run_number=run_number,
        run_id=os.getenv("GITHUB_RUN_ID"),
        mode="ai_test",
    )

    try:
        result = test_ai_provider(provider)
        detail = f"النموذج: {result['model']} — {result['reason']}"
        print(f"AI PROVIDER TEST PASSED: {result}")
        send_ai_test_status(
            result["provider"], True, detail, run_number
        )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(f"AI PROVIDER TEST FAILED: {detail}")
        try:
            send_ai_test_status(
                provider_name, False, detail, run_number
            )
        except Exception as telegram_exc:
            print(
                "Could not send failure result to Telegram: "
                f"{type(telegram_exc).__name__}: {telegram_exc}"
            )
        raise


if __name__ == "__main__":
    main()

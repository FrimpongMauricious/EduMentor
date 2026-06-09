"""
scripts/smoke_test.py — Post-deployment smoke test.

Usage:
    python scripts/smoke_test.py https://your-render-url.onrender.com

Verifies:
  1. /health returns 200
  2. /webhook/whatsapp accepts a POST and returns valid TwiML
  3. /webhook/ussd accepts a POST and returns a CON response
"""
import sys
import httpx


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test.py <base_url>")
        sys.exit(1)

    base = sys.argv[1].rstrip("/")
    print(f"Smoke testing: {base}")

    # 1. Health check
    print("\n[1/3] Health check...")
    r = httpx.get(f"{base}/health", timeout=30)
    assert r.status_code == 200, f"Health failed: {r.status_code}"
    print(f"  OK Health: {r.json()}")

    # 2. WhatsApp webhook
    print("\n[2/3] WhatsApp webhook...")
    r = httpx.post(
        f"{base}/webhook/whatsapp",
        data={"From": "whatsapp:+233244000111", "Body": "Hi"},
        timeout=30,
    )
    assert r.status_code == 200, f"WhatsApp failed: {r.status_code}"
    assert "<Response>" in r.text, f"Bad TwiML: {r.text[:200]}"
    print("  OK WhatsApp")

    # 3. USSD webhook
    print("\n[3/3] USSD webhook...")
    r = httpx.post(
        f"{base}/webhook/ussd",
        data={
            "sessionId": "smoke_test_1",
            "phoneNumber": "+233244000222",
            "networkCode": "62002",
            "serviceCode": "*384*25470#",
            "text": "",
        },
        timeout=30,
    )
    assert r.status_code == 200, f"USSD failed: {r.status_code}"
    assert r.text.startswith("CON "), f"Bad USSD response: {r.text[:200]}"
    print("  OK USSD")

    print("\nAll smoke tests passed!")


if __name__ == "__main__":
    main()

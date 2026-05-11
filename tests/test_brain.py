# -*- coding: utf-8 -*-
"""
JARVIS Neural Link Test Suite
Usage (서버 실행 전):  python tests/test_brain.py --offline
Usage (서버 실행 후):  python tests/test_brain.py
"""

import argparse
import os
import sys

# Windows 터미널 UTF-8 출력 강제
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# .env 로드
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')

import httpx
import anthropic

BASE_URL = "http://localhost:8000"
PASS = "\033[92m  [OK]\033[0m"
FAIL = "\033[91m  [FAIL]\033[0m"
INFO = "\033[94m  [-]\033[0m"


def section(title: str):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


# ── Test 1: API Key presence ──────────────────────────────────────────────────
def test_api_key():
    section("TEST 1 — API Key Validation")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        print(f"{FAIL} ANTHROPIC_API_KEY not found in .env")
        return False
    if key.startswith("sk-ant-여기에") or "여기에" in key:
        print(f"{FAIL} ANTHROPIC_API_KEY is still a placeholder — enter your real key in .env")
        return False
    masked = key[:12] + "..." + key[-4:]
    print(f"{PASS} Key loaded: {masked}")
    return True


# ── Test 2: Direct Anthropic API call ────────────────────────────────────────
def test_anthropic_direct():
    section("TEST 2 — Direct Anthropic Brain Connection")
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key or not key.isascii() or "여기에" in key:
        print(f"{FAIL} Skipping -- invalid or placeholder API key")
        return False
    try:
        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",   # 가장 가벼운 모델로 테스트
            max_tokens=64,
            system="You are JARVIS. Reply in one sentence, addressing the user as Sir.",
            messages=[{"role": "user", "content": "System check. Are you online?"}],
        )
        reply = response.content[0].text
        print(f"{PASS} Anthropic API responded")
        print(f"{INFO} JARVIS says: \"{reply}\"")
        return True
    except anthropic.AuthenticationError:
        print(f"{FAIL} Authentication failed -- check your ANTHROPIC_API_KEY")
    except anthropic.APIConnectionError as e:
        print(f"{FAIL} Network error: {str(e).encode('ascii', errors='replace').decode()}")
    except Exception as e:
        print(f"{FAIL} Unexpected error: {str(e).encode('ascii', errors='replace').decode()}")
    return False


# ── Test 3: Backend health endpoint ──────────────────────────────────────────
def test_backend_health():
    section("TEST 3 — Backend Server Liveness")
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=5)
        data = r.json()
        if r.status_code == 200 and data.get("alive"):
            print(f"{PASS} Server is alive at {BASE_URL}")
            key_ok = data.get("api_key_loaded")
            print(f"{PASS if key_ok else FAIL} API key loaded in server: {key_ok}")
            return True
        print(f"{FAIL} Unexpected response: {data}")
    except httpx.ConnectError:
        print(f"{FAIL} Cannot reach {BASE_URL} — is the server running?")
        print(f"{INFO} Start it with:  cd backend && uvicorn main:app --reload")
    except Exception as e:
        print(f"{FAIL} {e}")
    return False


# ── Test 4: Chat endpoint ─────────────────────────────────────────────────────
def test_chat_endpoint():
    section("TEST 4 — Chat Endpoint (POST /chat)")
    try:
        r = httpx.post(
            f"{BASE_URL}/chat",
            json={"message": "Status report, JARVIS."},
            timeout=30,
        )
        if r.status_code == 200:
            reply = r.json().get("response", "")
            print(f"{PASS} /chat responded (HTTP 200)")
            print(f"{INFO} JARVIS: \"{reply[:120]}{'...' if len(reply)>120 else ''}\"")
            return True
        print(f"{FAIL} HTTP {r.status_code}: {r.text}")
    except httpx.ConnectError:
        print(f"{FAIL} Server not reachable — run test_backend_health first")
    except Exception as e:
        print(f"{FAIL} {e}")
    return False


# ── Test 5: System status endpoint ───────────────────────────────────────────
def test_system_status():
    section("TEST 5 — System Monitor (GET /status)")
    try:
        r = httpx.get(f"{BASE_URL}/status", timeout=10)
        if r.status_code == 200:
            s = r.json()
            print(f"{PASS} /status responded")
            bat = s.get("battery_percent")
            print(f"{INFO} Battery : {bat}% {'(plugged)' if s.get('battery_plugged') else '(on battery)' if bat else '(N/A)'}")
            print(f"{INFO} CPU     : {s.get('cpu_percent')}%")
            print(f"{INFO} Memory  : {s.get('memory_percent')}%")
            alerts = s.get("alerts", [])
            if alerts:
                for a in alerts:
                    print(f"\033[93m  [!] ALERT: {a}\033[0m")
            return True
        print(f"{FAIL} HTTP {r.status_code}")
    except httpx.ConnectError:
        print(f"{FAIL} Server not reachable")
    except Exception as e:
        print(f"{FAIL} {e}")
    return False


# ── Runner ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline', action='store_true', help='Skip server-dependent tests')
    args = parser.parse_args()

    print("\n" + "="*52)
    print("      J.A.R.V.I.S  --  Neural Link Test Suite")
    print("="*52)

    results = []
    results.append(("API Key",        test_api_key()))
    results.append(("Anthropic API",  test_anthropic_direct()))

    if not args.offline:
        results.append(("Server Health", test_backend_health()))
        results.append(("Chat Endpoint", test_chat_endpoint()))
        results.append(("System Status", test_system_status()))
    else:
        print(f"\n{INFO} Skipping server tests (--offline mode)")

    section("SUMMARY")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        status = "  [OK]" if ok else "  [FAIL]"
        print(f"{status}  {name}")
    outcome = "ALL PASS" if passed == total else "SOME FAILED"
    print(f"\n  [{outcome}] {passed}/{total} tests passed")

    if passed == total:
        print("\n  [SUCCESS] System online. Neural link established, Sir.\n")
    else:
        print("\n  [WARNING] Some checks failed -- review above output, Sir.\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

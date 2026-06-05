"""
Diagnose the configured key: which API it belongs to, whether the project
allows the call, and the per-call token usage. Makes the smallest possible
requests (count_tokens + a 5-token generation). Never prints the full key.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from auto_tester.config import load_settings

s = load_settings()
key = s.gemini_api_key
if not key:
    print("No key found in env/.env (GEMINI_API_KEY).")
    sys.exit(2)

prefix = key[:4]
kind = {
    "AIza": "Gemini Developer API key",
    "AQ.A": "Vertex AI Express-mode API key",
    "ya29": "OAuth2 access token (short-lived)",
}.get(prefix, f"unknown (prefix '{prefix}…')")
print(f"key detected: {kind}  (len={len(key)})")
print(f"models: pro={s.model_pro}  flash={s.model_flash}\n")

from google import genai
from google.genai import types


def try_client(label, **client_kwargs):
    print(f"--- attempt: {label} ---")
    try:
        client = genai.Client(**client_kwargs)
    except Exception as e:
        print(f"  client init failed: {type(e).__name__}: {e}\n")
        return False
    # 1) cheapest possible auth check
    try:
        ct = client.models.count_tokens(model=s.model_flash, contents="ping")
        print(f"  count_tokens OK -> {getattr(ct, 'total_tokens', ct)}")
    except Exception as e:
        print(f"  count_tokens FAILED: {type(e).__name__}: {str(e)[:400]}\n")
        return False
    # 2) tiny generation to confirm generate + read usage
    try:
        resp = client.models.generate_content(
            model=s.model_flash,
            contents="Reply with the single word: ok",
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=5),
        )
        print(f"  generate_content OK -> {repr((resp.text or '').strip())[:60]}")
        um = getattr(resp, "usage_metadata", None)
        if um:
            print(f"  usage: prompt={getattr(um,'prompt_token_count',None)} "
                  f"output={getattr(um,'candidates_token_count',None)} "
                  f"total={getattr(um,'total_token_count',None)}")
    except Exception as e:
        print(f"  generate_content FAILED: {type(e).__name__}: {str(e)[:400]}\n")
        return False
    print("  => THIS MODE WORKS\n")
    return True


# Vertex Express first (key looks like AQ.*), then Developer API as fallback.
ok = False
if prefix.startswith("AQ"):
    ok = try_client("Vertex AI Express (vertexai=True, api_key=...)", vertexai=True, api_key=key)
    if not ok:
        ok = try_client("Gemini Developer API (api_key=...)", api_key=key)
else:
    ok = try_client("Gemini Developer API (api_key=...)", api_key=key)
    if not ok:
        ok = try_client("Vertex AI Express (vertexai=True, api_key=...)", vertexai=True, api_key=key)

print("RESULT:", "key works for at least one mode" if ok else "key did NOT work in any mode")

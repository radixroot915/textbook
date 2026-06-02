import json
import logging
import re
import time
import requests
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import (
    RESEARCHER_MODEL, QUERY_MODEL, OLLAMA_BASE, OLLAMA_TAGS,
    LLM_PROVIDER, LLM_API_KEY, LLM_API_BASE, LLM_MODEL,
)

log = logging.getLogger(__name__)



CONNECT_TIMEOUT = 10   # fail fast if the backend isn't accepting connections


def _call_ollama(model, prompt, temperature, timeout, retries, num_ctx, num_predict):
    options = {"temperature": temperature}
    if num_ctx is not None:
        options["num_ctx"] = num_ctx
    if num_predict is not None:
        options["num_predict"] = num_predict
    for attempt in range(retries):
        try:
            response = requests.post(
                OLLAMA_BASE,
                json={"model": model, "prompt": prompt, "stream": False,
                      "keep_alive": "10m", "options": options},
                timeout=(CONNECT_TIMEOUT, timeout),
            )
            if response.status_code in (429, 503):
                wait = 15 * (attempt + 1)
                log.warning(f"[LLM] Ollama busy ({response.status_code}), waiting {wait}s...")
                time.sleep(wait)
                continue
            text = response.json().get("response", "")
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            return text
        except requests.exceptions.ConnectTimeout:
            wait = 10 * (attempt + 1)
            log.warning(f"[LLM] Connect timeout (attempt {attempt+1}/{retries}), waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                log.error(f"[LLM] {model} failed after {retries} attempts: {e}")
                return ""
    return ""


def _call_openai_compatible(model, prompt, temperature, timeout, retries, num_predict):
    """OpenAI-compatible /chat/completions backend.

    Works with OpenAI, Groq, Together, OpenRouter, Ollama Cloud, vLLM,
    LM Studio, and any other server speaking the chat-completions schema.
    """
    if not LLM_API_KEY:
        log.error("[LLM] LLM_PROVIDER=openai but LLM_API_KEY is not set")
        return ""
    url = LLM_API_BASE.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL or model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "stream": False,
    }
    if num_predict is not None:
        payload["max_tokens"] = num_predict
    for attempt in range(retries):
        try:
            response = requests.post(
                url, json=payload, headers=headers,
                timeout=(CONNECT_TIMEOUT, timeout),
            )
            if response.status_code in (429, 503):
                wait = 15 * (attempt + 1)
                log.warning(f"[LLM] API busy ({response.status_code}), waiting {wait}s...")
                time.sleep(wait)
                continue
            if response.status_code >= 400:
                log.error(f"[LLM] API HTTP {response.status_code}: {response.text[:300]}")
                return ""
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            return text
        except requests.exceptions.ConnectTimeout:
            wait = 10 * (attempt + 1)
            log.warning(f"[LLM] Connect timeout (attempt {attempt+1}/{retries}), waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                log.error(f"[LLM] {model} failed after {retries} attempts: {e}")
                return ""
    return ""


def call(model, prompt, temperature=0.3, timeout=240, retries=1, num_ctx=4096, num_predict=512):
    if LLM_PROVIDER == "openai":
        return _call_openai_compatible(model, prompt, temperature, timeout, retries, num_predict)
    return _call_ollama(model, prompt, temperature, timeout, retries, num_ctx, num_predict)


def call_json(model, prompt, temperature=0.3, timeout=240, retries=1, num_ctx=4096, num_predict=512):
    raw = call(model, prompt, temperature=temperature, timeout=timeout, retries=retries, num_ctx=num_ctx, num_predict=num_predict)
    if not raw:
        return None
    # Strip qwen3 thinking blocks
    cleaned = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    # Strip markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*', '', cleaned).strip().rstrip('`').strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    # Try object first (more specific than array)
    m = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # Try array
    m = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def health_check():
    if LLM_PROVIDER == "openai":
        if not LLM_API_KEY:
            log.error("[!] LLM_PROVIDER=openai but LLM_API_KEY not set")
            return False
        try:
            r = requests.get(
                LLM_API_BASE.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                timeout=10,
            )
            if r.status_code == 200:
                log.info(f"[OK] LLM API reachable at {LLM_API_BASE} (model={LLM_MODEL or RESEARCHER_MODEL})")
                return True
            # Some endpoints don't expose /models; treat as soft pass.
            log.warning(f"[!] /models returned {r.status_code} — proceeding anyway")
            return True
        except Exception as e:
            log.error(f"[!] LLM API not accessible: {e}")
            return False
    try:
        r = requests.get(OLLAMA_TAGS, timeout=5)
        if r.status_code != 200:
            log.warning(f"[!] Ollama responded with status {r.status_code}")
            return False
        models = [m.get("name", "") for m in r.json().get("models", [])]
        log.info(f"[OK] Ollama running. Models: {models}")
        missing = []
        from config import SEED_MODEL
        for needed in [QUERY_MODEL, RESEARCHER_MODEL, SEED_MODEL]:
            if not any(needed in m for m in models):
                missing.append(needed)
        if missing:
            log.warning(f"[!] Missing models: {missing}. Run: ollama pull {missing[0]}")
        return True
    except Exception as e:
        log.error(f"[!] Ollama not accessible: {e}")
        return False

import json
import logging
import re
import time
import requests
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config import RESEARCHER_MODEL, QUERY_MODEL, OLLAMA_BASE, OLLAMA_TAGS

log = logging.getLogger(__name__)



def call(model, prompt, temperature=0.3, timeout=60, retries=1, num_ctx=4096, num_predict=512):
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
                timeout=timeout
            )
            return response.json().get("response", "")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                log.error(f"[LLM] {model} failed after {retries} attempts: {e}")
                return ""
    return ""


def call_json(model, prompt, temperature=0.3, timeout=60, retries=1, num_ctx=4096, num_predict=512):
    raw = call(model, prompt, temperature=temperature, timeout=timeout, retries=retries, num_ctx=num_ctx, num_predict=num_predict)
    if not raw:
        return None
    # Strip markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip().rstrip('`').strip()
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

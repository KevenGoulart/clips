"""Conversa com o modelo local do Ollama, sempre com resposta em JSON (schema)."""
import json
import re

import requests

from .config import OLLAMA_MODEL, OLLAMA_URL


def check():
    try:
        tags = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).json()
    except requests.RequestException:
        raise RuntimeError("Ollama não está rodando. Abra o app Ollama ou rode 'ollama serve'.")
    names = [m["name"] for m in tags.get("models", [])]
    if not any(n == OLLAMA_MODEL or n.startswith(OLLAMA_MODEL + ":") for n in names):
        raise RuntimeError(f"Modelo '{OLLAMA_MODEL}' não instalado. Rode: ollama pull {OLLAMA_MODEL}")


def chat(system: str, user: str, schema: dict, think=False, temperature=0.3, num_ctx=8192, timeout=900) -> dict:
    resp = requests.post(f"{OLLAMA_URL}/api/chat", timeout=timeout, json={
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": schema,
        "think": think,  # sem raciocínio (qwen3 etc.) a resposta é bem mais rápida
        "keep_alive": "5m",
        "options": {"temperature": temperature, "num_ctx": num_ctx},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    })
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        try:
            return json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:  # resposta cortada
            return {}


def unload():
    """Tira o modelo da VRAM antes da narração (o Chatterbox precisa da GPU)."""
    try:
        requests.post(f"{OLLAMA_URL}/api/generate", json={"model": OLLAMA_MODEL, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass

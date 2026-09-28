"""OpenAI-compatible image generation request helper."""

from __future__ import annotations

import base64
import json
import urllib.request


def generate_image(endpoint: str, model: str, api_key: str, prompt: str) -> bytes:
    url = endpoint.rstrip("/")
    if not url.endswith("/images/generations"):
        url += "/images/generations"
    payload = json.dumps({"model": model, "prompt": prompt, "n": 1, "size": "1024x1024", "response_format": "b64_json"}).encode("utf-8")
    request = urllib.request.Request(url, data=payload, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read(20_000_000).decode("utf-8"))
    images = data.get("data") or []
    if not images:
        raise ValueError("图像服务没有返回图片。")
    if images[0].get("b64_json"):
        return base64.b64decode(images[0]["b64_json"], validate=True)
    if images[0].get("url"):
        with urllib.request.urlopen(images[0]["url"], timeout=60) as response:
            return response.read(20_000_000)
    raise ValueError("图像服务返回格式不受支持，需要 data[0].b64_json 或 data[0].url。")

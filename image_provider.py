"""OpenAI-compatible image generation request helper."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import uuid
import urllib.request


def generate_image(endpoint: str, model: str, api_key: str, prompt: str, reference_image: str = "") -> bytes:
    url = endpoint.rstrip("/")
    suffix = "/images/edits" if reference_image else "/images/generations"
    if not url.endswith(suffix):
        if url.endswith("/images/generations") or url.endswith("/images/edits"): url = url.rsplit("/", 2)[0] + suffix
        else: url += suffix
    fields = {"model": model, "prompt": prompt, "n": "1", "size": "1024x1024", "response_format": "b64_json"}
    if reference_image:
        boundary = uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), str(value).encode("utf-8"), b"\r\n"))
        filename = os.path.basename(reference_image); mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(reference_image, "rb") as image_file: image_data = image_file.read()
        chunks.extend((f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'.encode(), f"Content-Type: {mime}\r\n\r\n".encode(), image_data, b"\r\n", f"--{boundary}--\r\n".encode()))
        payload = b"".join(chunks); content_type = f"multipart/form-data; boundary={boundary}"
    else:
        payload = json.dumps({**fields, "n": 1}).encode("utf-8"); content_type = "application/json"
    request = urllib.request.Request(url, data=payload, method="POST", headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type, "Accept": "application/json"})
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

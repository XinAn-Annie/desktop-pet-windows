"""Local RSS/Atom and public-page update checks for the desktop pet."""

from __future__ import annotations

import hashlib
from html import unescape
import re
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.title = []; self.description = ""; self.text = []; self.feed_links = []; self._in_title = False; self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title": self._in_title = True
        if tag in ("script", "style", "noscript"): self._skip += 1
        if tag == "meta" and attrs.get("name", "").lower() in ("description", "og:description"):
            self.description = attrs.get("content", "")
        if tag == "link" and "alternate" in attrs.get("rel", "").lower() and any(x in attrs.get("type", "").lower() for x in ("rss", "atom", "xml")):
            self.feed_links.append(attrs.get("href", ""))

    def handle_endtag(self, tag):
        if tag == "title": self._in_title = False
        if tag in ("script", "style", "noscript"): self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        clean = " ".join(data.split())
        if not clean: return
        if self._in_title: self.title.append(clean)
        if not self._skip: self.text.append(clean)


def summarize(text: str, limit: int = 260) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if not text: return "来源没有提供文字简介。"
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", text) if part.strip()]
    summary = " ".join(sentences[:2]) if len(sentences) > 1 else text
    return summary if len(summary) <= limit else summary[: limit - 1].rstrip() + "…"


def _request(url: str) -> tuple[bytes, str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入以 http:// 或 https:// 开头的网址。")
    request = urllib.request.Request(url, headers={"User-Agent": "DesktopPet/0.1 (RSS update checker)", "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.7"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read(4_000_000), response.headers.get_content_type(), response.geturl()


def _parse_feed(data: bytes) -> dict | None:
    try: root = ET.fromstring(data)
    except ET.ParseError: return None
    def local(tag): return tag.rsplit("}", 1)[-1].lower()
    elements = list(root.iter())
    entries = [element for element in elements if local(element.tag) in ("item", "entry")]
    if not entries: return None
    item = entries[0]
    fields = {}
    for child in item.iter():
        name = local(child.tag)
        if name in ("title", "description", "summary", "content", "encoded", "guid", "id", "link", "updated", "published") and name not in fields:
            fields[name] = " ".join((child.itertext() and " ".join(child.itertext()).split()) or [])
            if name == "link" and child.attrib.get("href"): fields[name] = child.attrib["href"]
    title = fields.get("title", "新内容")
    body = fields.get("summary") or fields.get("description") or fields.get("content") or fields.get("encoded") or title
    identity = fields.get("guid") or fields.get("id") or fields.get("link") or fields.get("published") or title
    return {"title": title, "summary": summarize(body), "link": fields.get("link", ""), "latest_id": hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()}


def fetch_latest(url: str) -> dict:
    data, content_type, final_url = _request(url)
    feed = _parse_feed(data)
    if not feed and ("html" in content_type or data.lstrip().startswith((b"<!", b"<html", b"<HTML"))):
        parser = PageParser(); parser.feed(data.decode("utf-8", errors="replace"))
        for href in parser.feed_links:
            if href:
                feed_url = urljoin(final_url, href)
                try:
                    feed_data, _, _ = _request(feed_url); feed = _parse_feed(feed_data)
                    if feed:
                        feed["feed_url"] = feed_url; break
                except Exception:
                    continue
        if not feed:
            body = parser.description or " ".join(parser.text)
            page_title = " ".join(parser.title).strip() or final_url
            identity = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
            feed = {"title": page_title, "summary": summarize(body), "link": final_url, "latest_id": identity, "page_mode": True}
    if not feed:
        raise ValueError("页面没有检测到 RSS/Atom 条目，且无法提取网页文字。请添加 RSS/Atom 订阅地址。")
    feed["source_url"] = url
    return feed

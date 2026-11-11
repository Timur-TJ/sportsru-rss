#!/usr/bin/env python3
"""Convert Sports.ru football blog listings to RSS 2.0. Python 3.9+."""
import argparse
import gzip
import json
import ssl
import time
from html import escape
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

API = "https://www.sports.ru/core/post/list/"
PAGE = "https://www.sports.ru/football/blogs/"

# Маркеры для исключения материалов о российском футболе
RU_STOPWORDS = {
    "рпл", "спартак", "зенит", "цска", "локомотив", "краснодар",
    "динамо москва", "ростов", "рубин", "крылья советов", "ахмат",
    "пари нн", "факел", "оренбург", "балтика", "сочи", "химки",
    "сборная россии", "первая лига", "фнл", "кубок россии", "дзюба"
}


def is_russian_football(post):
    """Проверяет, относится ли пост к российскому футболу по заголовку и тегам."""
    tags = " ".join(t.get("name", "").lower() for t in post.get("tags", []))
    title = post.get("title", "").lower()
    brief = (post.get("brief") or "").lower()
    blog_name = post.get("blog", {}).get("name", "").lower()

    text_to_check = f"{title} {tags} {brief} {blog_name}"
    return any(stopword in text_to_check for stopword in RU_STOPWORDS)


def fetch_posts(mode, pages, hours=48):
    posts = {}
    cursor = None
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    context = ssl.create_default_context()
    # Some macOS Python installations do not configure a default CA bundle.
    if ssl.get_default_verify_paths().cafile is None and Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    for _ in range(pages):
        args = {"filter": {"type": "section-name", "name": "football",
                           "sub-name": "", "article_type": mode}}
        if cursor is not None:
            args["feeding"] = {"type": "time", "last_published": cursor}
        url = API + "?" + urlencode({"args": json.dumps(args, separators=(",", ":"))})
        request = Request(url, headers={"User-Agent": "SportsBlogRSS/1.0",
                                        "Accept": "application/json"})
        with urlopen(request, timeout=30, context=context) as response:
            body = response.read()
        if body.startswith(b"\x1f\x8b"):
            body = gzip.decompress(body)
        data = json.loads(body)
        if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
            raise ValueError("Unexpected API response; previous RSS file was preserved")
        for post in data["documents"]:
            if not post.get("is_draft") and not post.get("is_hidden"):
                if post["published"]["timestamp"] >= cutoff:
                    # Проверяем на стоп-слова перед добавлением
                    if not is_russian_football(post):
                        posts[str(post["id"])] = post
        next_cursor = data.get("last_published")
        if not data.get("has_more_documents") or (next_cursor and next_cursor <= cutoff):
            break
        if not next_cursor or (cursor is not None and next_cursor >= cursor):
            raise ValueError("API pagination stopped advancing; previous RSS file was preserved")
        cursor = next_cursor
        time.sleep(0.3)
    else:
        raise ValueError("Page limit reached before the requested time window; increase --pages")
    if not posts:
        raise ValueError("No posts returned; previous RSS file was preserved")
    return sorted(posts.values(), key=lambda p: p["published"]["timestamp"], reverse=True)


def build_feed(posts, mode):
    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    for tag, value in [
        ("title", "Sports.ru — европейский футбол: " + ("новые статьи" if mode == "all" else "популярные статьи")),
        ("link", PAGE + ("#new" if mode == "all" else "#best")),
        ("description", "Лента статей из раздела блогов Sports.ru (без РПЛ). Неофициальный конвертер."),
        ("language", "ru"), ("ttl", "720"),
        ("lastBuildDate", format_datetime(datetime.now(timezone.utc))),
    ]:
        ET.SubElement(channel, tag).text = value
    for post in posts:
        item = ET.SubElement(channel, "item")
        url = post["desktop_url"]
        ET.SubElement(item, "title").text = post["title"]
        ET.SubElement(item, "link").text = url
        ET.SubElement(item, "guid", isPermaLink="true").text = url
        published = datetime.fromtimestamp(post["published"]["timestamp"], timezone.utc)
        ET.SubElement(item, "pubDate").text = format_datetime(published)
        image_url = (post.get("feed_image") or {}).get("link") or post.get("media_src")
        description = []
        if image_url and image_url.startswith("https://"):
            description.append('<p><img src="' + escape(image_url, quote=True) + '" alt=""></p>')
        description.append("<p>" + escape(post.get("brief") or "") + "</p>")
        description.append('<p><a href="' + escape(url, quote=True) + '">Читать статью на Sports.ru</a></p>')
        ET.SubElement(item, "description").text = "".join(description)
        if post.get("blog", {}).get("name"):
            ET.SubElement(item, "category").text = post["blog"]["name"]
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, default=100, help="Maximum API pages, 1–100 (default: 100)")
    parser.add_argument("--hours", type=int, default=48, help="Publication window in hours (default: 48)")
    parser.add_argument("--mode", choices=["new", "best"], default="new")
    parser.add_argument("--output", type=Path, default=Path("sports-football.xml"))
    args = parser.parse_args()
    if not 1 <= args.pages <= 100:
        parser.error("--pages must be between 1 and 100")
    if not 1 <= args.hours <= 720:
        parser.error("--hours must be between 1 and 720")
    mode = "all" if args.mode == "new" else "best"
    posts = fetch_posts(mode, args.pages, args.hours)
    xml = build_feed(posts, mode)
    ET.fromstring(xml)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(xml)
    temporary.replace(args.output)
    print(f"Saved {len(posts)} articles to {args.output}")


if __name__ == "__main__":
    main()

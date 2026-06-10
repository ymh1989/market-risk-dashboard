import argparse
import email.utils
import html
import json
import os
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "news_digest_keywords.json"
ENV_FILE = ROOT / ".env"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
USER_AGENT = "Mozilla/5.0 (compatible; market-lab-news-digest/0.1)"
KST = timezone(timedelta(hours=9), "KST")
TELEGRAM_LIMIT = 4096


@dataclass
class Article:
    topic: str
    title: str
    link: str
    source: str
    published_at: datetime


def load_env_file(path=ENV_FILE):
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_int(name, default):
    raw_value = os.environ.get(name)
    if raw_value is None or raw_value == "":
        return default
    try:
        return int(raw_value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer")


def read_topics(config_path):
    topics = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(topics, list) or not topics:
        raise SystemExit("news keyword config must be a non-empty list")
    for topic in topics:
        if not topic.get("name") or not topic.get("query"):
            raise SystemExit("each news keyword config item needs name and query")
    return topics


def build_rss_url(query):
    params = {
        "q": f"({query}) when:1d",
        "hl": "ko",
        "gl": "KR",
        "ceid": "KR:ko",
    }
    return f"{GOOGLE_NEWS_RSS_URL}?{urllib.parse.urlencode(params)}"


def text_or_empty(element, name):
    child = element.find(name)
    return child.text.strip() if child is not None and child.text else ""


def parse_published_at(value):
    if not value:
        return datetime.now(timezone.utc)
    parsed = email.utils.parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_topic_articles(topic, lookback_hours):
    url = build_rss_url(topic["query"])
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        rss = response.read()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    root = ET.fromstring(rss)
    articles = []
    for item in root.findall("./channel/item"):
        published_at = parse_published_at(text_or_empty(item, "pubDate"))
        if published_at < cutoff:
            continue
        source_element = item.find("source")
        articles.append(
            Article(
                topic=topic["name"],
                title=text_or_empty(item, "title"),
                link=text_or_empty(item, "link"),
                source=source_element.text.strip() if source_element is not None and source_element.text else "뉴스",
                published_at=published_at,
            )
        )
    return articles


def collect_articles(topics, lookback_hours, top_per_topic, max_items):
    collected = []
    seen = set()
    for topic in topics:
        try:
            topic_articles = fetch_topic_articles(topic, lookback_hours)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as error:
            print(f"[warn] {topic['name']} fetch failed: {error}", file=sys.stderr)
            continue

        topic_articles.sort(key=lambda article: article.published_at, reverse=True)
        for article in topic_articles:
            dedupe_key = article.link or article.title
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            collected.append(article)
            if sum(1 for item in collected if item.topic == topic["name"]) >= top_per_topic:
                break
    return collected[:max_items]


def format_digest(articles, topics, lookback_hours):
    now_kst = datetime.now(KST)
    topic_names = ", ".join(topic["name"] for topic in topics)
    lines = [
        "<b>리스크 뉴스 브리핑</b>",
        f"{html.escape(now_kst.strftime('%Y-%m-%d %H:%M'))} KST / 최근 {lookback_hours}시간",
        f"키워드: {html.escape(topic_names)}",
        "",
    ]
    if not articles:
        lines.append("조건에 맞는 최신 기사를 찾지 못했습니다.")
        return "\n".join(lines)

    by_topic = defaultdict(list)
    for article in articles:
        by_topic[article.topic].append(article)

    for topic in [topic["name"] for topic in topics]:
        topic_articles = by_topic.get(topic, [])
        if not topic_articles:
            continue
        lines.append(f"<b>{html.escape(topic)}</b>")
        for index, article in enumerate(topic_articles, start=1):
            published_kst = article.published_at.astimezone(KST).strftime("%m-%d %H:%M")
            lines.extend(
                [
                    f"{index}. {html.escape(article.title)}",
                    f"{html.escape(article.source)} · {html.escape(published_kst)}",
                    html.escape(article.link),
                    "",
                ]
            )
    return "\n".join(lines).strip()


def split_message(message):
    paragraphs = message.split("\n\n")
    chunks = []
    current = ""
    for paragraph in paragraphs:
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) <= TELEGRAM_LIMIT:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = textwrap.shorten(paragraph, width=TELEGRAM_LIMIT - 20, placeholder=" ...")
    if current:
        chunks.append(current)
    return chunks


def send_telegram(message, token, chat_id):
    for chunk in split_message(message):
        data = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            TELEGRAM_SEND_URL.format(token=token),
            data=data,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram send failed: {payload}")


def parse_args():
    parser = argparse.ArgumentParser(description="Send a daily risk-news digest to Telegram.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="keyword config JSON path")
    parser.add_argument("--lookback-hours", type=int, default=env_int("NEWS_DIGEST_LOOKBACK_HOURS", 24))
    parser.add_argument("--top-per-topic", type=int, default=env_int("NEWS_DIGEST_TOP_PER_TOPIC", 4))
    parser.add_argument("--max-items", type=int, default=env_int("NEWS_DIGEST_MAX_ITEMS", 20))
    parser.add_argument("--dry-run", action="store_true", help="print the digest instead of sending it")
    return parser.parse_args()


def main():
    load_env_file()
    args = parse_args()
    topics = read_topics(args.config)
    articles = collect_articles(topics, args.lookback_hours, args.top_per_topic, args.max_items)
    message = format_digest(articles, topics, args.lookback_hours)

    if args.dry_run:
        print(message)
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required. Copy .env.example to .env first.")

    send_telegram(message, token, chat_id)
    print(f"Sent {len(articles)} articles to Telegram")


if __name__ == "__main__":
    main()

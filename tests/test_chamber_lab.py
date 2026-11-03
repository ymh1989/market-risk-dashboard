"""독립 HTML 재현성과 기존 이스터에그 연결을 검증합니다."""

import base64
from html.parser import HTMLParser
import json
from pathlib import Path

from scripts.build_chamber_lab import build_html

ROOT = Path(__file__).resolve().parents[1]


class Dependencies(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external = []
        self.engine = False
        self.payload = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "img", "iframe", "link", "audio"} and (attrs.get("src") or attrs.get("href")):
            self.external.append(attrs)
        self.engine = tag == "script" and attrs.get("id") == "engine"

    def handle_data(self, data):
        if self.engine:
            self.payload += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.engine = False


def test_single_file_is_reproducible_and_offline():
    html = build_html()
    assert (ROOT / "chamber-lab.html").read_text() == html
    assert html == build_html()
    parser = Dependencies()
    parser.feed(html)
    assert parser.external == []
    engine = json.loads(parser.payload)
    assert base64.b64decode(engine["core"]) == (ROOT / "src/vendor/three.core.min.js").read_bytes()
    assert base64.b64decode(engine["renderer"]) == (ROOT / "src/vendor/three.module.min.js").read_bytes()
    assert "Lucide Icons and Contributors" in html
    assert "three.js authors" in html


def test_existing_lab_has_accessible_entry():
    html = (ROOT / "snow-lab.html").read_text()
    assert 'href="./chamber-lab.html"' in html
    assert 'aria-label="숨겨진 방 열기"' in html
    for mode in ("snow", "wave", "spectrum", "obstacle", "forest"):
        assert f'data-mode-select="{mode}"' in html

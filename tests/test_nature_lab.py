"""자연 감상 화면의 빌드 재현성·자산 격리·기존 입구를 검증합니다."""

from html.parser import HTMLParser
from pathlib import Path
import struct

import pytest

from scripts.build_nature_lab import build_html

ROOT = Path(__file__).resolve().parents[1]


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_html_is_reproducible_and_has_no_remote_runtime_dependencies():
    html = build_html()
    assert html == build_html() == (ROOT / "nature-lab.html").read_text()
    page = Page()
    page.feed(html)
    for tag, attrs in page.tags:
        if tag in {"video", "script", "iframe", "img", "link"}:
            url = attrs.get("src") or attrs.get("href") or ""
            assert not url.startswith(("https://", "http://", "//"))
    assert "Lucide Icons and Contributors" in html
    assert not any(tag == "iframe" for tag, _ in page.tags)


def test_initial_video_sources_are_lazy_and_muted():
    page = Page()
    page.feed(build_html())
    videos = [attrs for tag, attrs in page.tags if tag == "video"]
    assert len(videos) == 2
    assert all("muted" in v and "playsinline" in v and v["preload"] == "none" and "src" not in v for v in videos)
    assert "prefers-reduced-motion" in build_html()
    assert "visibilitychange" in build_html()


@pytest.mark.parametrize("name", ["forest", "water", "alps"])
def test_media_is_local_compact_and_faststart(name):
    path = ROOT / f"src/media/nature/{name}.mp4"
    assert 100_000 < path.stat().st_size < 15_000_000
    assert (ROOT / f"src/media/nature/{name}.jpg").stat().st_size > 10_000
    boxes = []
    with path.open("rb") as stream:
        while header := stream.read(8):
            length, kind = struct.unpack(">I4s", header)
            assert length >= 8
            boxes.append(kind)
            stream.seek(length - 8, 1)
    assert boxes.index(b"moov") < boxes.index(b"mdat")


def test_existing_labs_link_to_nature_without_replacing_simulations():
    for file in ("snow-lab.html", "chamber-lab.html"):
        text = (ROOT / file).read_text()
        assert 'href="./nature-lab.html"' in text and 'aria-label="창밖 열기"' in text
    assert 'href="./chamber-lab.html"' in (ROOT / "snow-lab.html").read_text()
    assert "nature-lab" not in (ROOT / "src/app.js").read_text()
    assert "src/media/nature" not in (ROOT / "index.html").read_text()


def test_source_documentation_and_licenses_are_present():
    text = (ROOT / "docs/nature-lab.md").read_text()
    for number in (5008, 28342, 4132):
        assert str(number) in text
    assert "3D" in text and "오프라인" in text and "Mixkit" in text

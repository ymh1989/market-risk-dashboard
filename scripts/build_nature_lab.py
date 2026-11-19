"""자연 감상 화면의 코드와 기존 Lucide 아이콘을 하나의 HTML로 묶습니다."""

from pathlib import Path
import json
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def build_html(root: Path = ROOT) -> str:
    """영상은 별도 로컬 자산으로 유지하여 필요한 장면만 읽도록 합니다."""
    icons = {}
    for name, elements in json.loads((root / "src/vendor/chamber-icons.json").read_text()).items():
        svg = ET.Element("svg", {"viewBox": "0 0 24 24", "aria-hidden": "true"})
        for tag, attrs in elements:
            ET.SubElement(svg, tag, attrs)
        icons[name] = ET.tostring(svg, encoding="unicode")
    html = (root / "src/nature-lab.html").read_text()
    for name, icon in icons.items():
        html = html.replace("{{ICON_" + name.upper() + "}}", icon)
    values = {"STYLE": (root / "src/nature-lab.css").read_text(),
              "SCRIPT": (root / "src/nature-lab.js").read_text(),
              "ICONS": json.dumps(icons),
              "LICENSE": (root / "src/vendor/lucide.LICENSE").read_text().replace("--", "- -")}
    for name, value in values.items():
        html = html.replace("{{" + name + "}}", value)
    if "{{" in html:
        raise ValueError("자연 감상 화면에 미완성 항목이 남았습니다.")
    return html


if __name__ == "__main__":
    output = ROOT / "nature-lab.html"
    output.write_text(build_html(), encoding="utf-8")
    print(f"자연 감상 화면 생성: {output.name}")

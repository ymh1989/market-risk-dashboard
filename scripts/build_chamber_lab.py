"""고정된 Three.js와 작품 소스를 외부 요청 없는 단일 HTML로 묶습니다."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def build_html(root: Path = ROOT) -> str:
    """동일한 입력으로 동일한 HTML을 생성하며 실행 시 빌드 도구는 필요 없습니다."""
    vendor = root / "src/vendor"
    renderer = (vendor / "three.module.min.js").read_bytes()
    if renderer.count(b'"./three.core.min.js"') != 2:
        raise ValueError("Three.js 모듈 구성이 바뀌었습니다. 내장 경로를 다시 검증해야 합니다.")
    engine = {
        "core": base64.b64encode((vendor / "three.core.min.js").read_bytes()).decode("ascii"),
        "renderer": base64.b64encode(renderer).decode("ascii"),
    }
    icons = {}
    for name, elements in json.loads((vendor / "chamber-icons.json").read_text()).items():
        svg = ET.Element("svg", {"viewBox":"0 0 24 24", "aria-hidden":"true"})
        for tag, attrs in elements:
            ET.SubElement(svg, tag, attrs)
        icons[name] = ET.tostring(svg, encoding="unicode")
    template = (root / "src/chamber-lab.html").read_text()
    for name, svg in icons.items():
        template = template.replace("{{ICON_" + name.upper() + "}}", svg)
    replacements = {
        "LICENSES": (vendor / "three.LICENSE").read_text() + "\n" + (vendor / "lucide.LICENSE").read_text(),
        "ENGINE": json.dumps(engine, separators=(",", ":")),
        "ICONS": json.dumps(icons, separators=(",", ":")),
        "BOOTSTRAP": (root / "src/chamber-lab.js").read_text(),
    }
    for name, text in replacements.items():
        template = template.replace("{{" + name + "}}", text.replace("</script", "<\\/script") if name != "LICENSES" else text.replace("--", "- -"))
    if "{{" in template:
        raise ValueError("HTML에 미완성 항목이 남아 있습니다.")
    return template


def main() -> None:
    """저장소 루트에 바로 열 수 있는 완성본을 생성합니다."""
    parser = argparse.ArgumentParser(description="Field Lab 단일 HTML 생성")
    parser.add_argument("--output", type=Path, default=ROOT / "chamber-lab.html")
    args = parser.parse_args()
    args.output.write_text(build_html(), encoding="utf-8")
    print(f"단일 HTML 생성 완료: {args.output} ({args.output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

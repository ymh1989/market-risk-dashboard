from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT = ROOT / "reports" / "market-risk-dashboard-offline.html"
DATA_FILES = [
    "data/risk-dashboard.json",
    "data/market-risk-timeseries.json",
    "data/ml-risk-signal.json",
    "data/els-index-risk.json",
    "data/hmm-regime.json",
    "data/pipeline-status.json",
    "data/market-risk-snapshot.json",
    "data/data-quality.json",
    "data/naver-marketindex-history.json",
    "data/market-risk-backtest.json",
    "data/market-stress-episodes.json",
    "data/kospi-breadth.json",
]


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(read_text(path))


def snapshot_stamp(dashboard: dict[str, Any]) -> str:
    generated_at = str((dashboard.get("metadata") or {}).get("generatedAt") or "")
    match = re.search(
        r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
        r"(?:[ T](?P<hour>\d{2}):(?P<minute>\d{2}))?",
        generated_at,
    )
    if not match:
        return datetime.now(KST).strftime("%Y%m%d-%H%M-KST")
    groups = match.groupdict(default="00")
    return (
        f"{groups['year']}{groups['month']}{groups['day']}"
        f"-{groups['hour']}{groups['minute']}-KST"
    )


def javascript_json(payload: dict[str, Any]) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def offline_app_source(data_payloads: dict[str, dict[str, Any]]) -> str:
    risk_model_source = re.sub(
        r"^export\s+",
        "",
        read_text(ROOT / "src" / "risk-model.js"),
        flags=re.MULTILINE,
    )
    app_source = read_text(ROOT / "src" / "app.js")
    app_source = re.sub(
        r'^import\s+\{[^}]+\}\s+from\s+"\.\/risk-model\.js";\s*',
        "",
        app_source,
        count=1,
    )
    loader_start = app_source.index("async function loadJson(")
    loader_end = app_source.index("\n\nPromise.all([", loader_start)
    embedded_data = javascript_json(data_payloads)
    offline_loader = f"""const OFFLINE_DATA = Object.freeze({embedded_data});

async function loadJson(path, required = false) {{
  const normalizedPath = String(path).replace(/^\\.\\//, "").split("?", 1)[0];
  if (Object.prototype.hasOwnProperty.call(OFFLINE_DATA, normalizedPath)) {{
    return OFFLINE_DATA[normalizedPath];
  }}
  const error = new Error(`${{path}} 오프라인 스냅샷에 포함되지 않았습니다.`);
  if (required) throw error;
  console.warn(error.message);
  return null;
}}"""
    return f"{risk_model_source}\n\n{app_source[:loader_start]}{offline_loader}{app_source[loader_end:]}"


def build_offline_html() -> tuple[str, str]:
    dashboard = read_json(ROOT / "data" / "risk-dashboard.json")
    data_payloads = {
        relative_path: read_json(ROOT / relative_path)
        for relative_path in DATA_FILES
    }
    html = read_text(ROOT / "index.html")
    styles = read_text(ROOT / "src" / "styles.css")
    styles = re.sub(
        r'url\("https://images\.unsplash\.com/[^"]+"\)',
        "linear-gradient(135deg, #17323b, #1c252b)",
        styles,
    )
    styles += """

/* 독립형 스냅샷에서는 온라인 전용 기능을 숨깁니다. */
.snow-lab-trigger,
.hero__download,
.is-offline-snapshot .operation-status-strip,
.is-offline-snapshot [data-tab="operations"],
.is-offline-snapshot [data-tab="model-monitoring"],
.is-offline-snapshot [data-panel="operations"],
.is-offline-snapshot [data-panel="model-monitoring"] {
  display: none !important;
}
"""
    script = offline_app_source(data_payloads).replace("</script", "<\\/script")
    html = re.sub(
        r'\s*<link rel="stylesheet" href="[^"]*styles\.css[^"]*" />',
        lambda _: f"\n    <style>{styles}</style>",
        html,
        count=1,
    )
    html = re.sub(
        r'\s*<script type="module" src="[^"]*app\.js[^"]*"></script>',
        lambda _: f"\n    <script>{script}</script>",
        html,
        count=1,
    )
    html = html.replace(
        "<title>Integrated Risk Monitoring Dashboard</title>",
        "<title>시장 리스크 대시보드 오프라인 스냅샷</title>",
    )
    html = html.replace(
        '<html lang="ko">',
        '<html lang="ko" class="is-offline-snapshot">',
        1,
    )
    html = html.replace(
        "</head>",
        (
            '    <meta name="offline-snapshot" content="true" />\n'
            f'    <meta name="snapshot-stamp" content="{snapshot_stamp(dashboard)}" />\n'
            "  </head>"
        ),
    )
    return html, snapshot_stamp(dashboard)


def write_snapshot(
    output: Path,
    *,
    timestamped_copy: bool,
) -> list[Path]:
    html, stamp = build_offline_html()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    output.chmod(0o644)
    written = [output]
    if timestamped_copy:
        timestamped_output = output.with_name(f"market-risk-dashboard-{stamp}.html")
        if timestamped_output != output:
            timestamped_output.write_text(html, encoding="utf-8")
            timestamped_output.chmod(0o644)
            written.append(timestamped_output)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="서버 없이 열 수 있는 단일 HTML 시장리스크 스냅샷을 생성합니다."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--stable-only",
        action="store_true",
        help="고정 파일만 생성하고 날짜·시간 사본은 만들지 않습니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs = write_snapshot(
        args.output.resolve(),
        timestamped_copy=not args.stable_only,
    )
    for output in outputs:
        print(f"Wrote offline dashboard: {output} ({output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()

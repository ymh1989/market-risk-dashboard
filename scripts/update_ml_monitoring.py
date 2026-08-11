from __future__ import annotations

import json
from pathlib import Path

from kospi_risk.model_monitoring import build_model_monitoring, enrich_walk_forward_results


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_FILE = ROOT / "data" / "ml-risk-signal.json"


def main() -> None:
    payload = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
    rows = enrich_walk_forward_results(
        payload.get("walkForwardSeries", []),
        payload.get("series", []),
    )
    payload["walkForwardSeries"] = rows
    payload["monitoring"] = build_model_monitoring(rows, payload.get("series", []))
    PAYLOAD_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"ML 운영 모니터링 갱신: {PAYLOAD_FILE}")


if __name__ == "__main__":
    main()

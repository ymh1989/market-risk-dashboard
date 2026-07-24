import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
DEFAULT_OUTPUT = ROOT / "data" / "pipeline-status.json"
DATA_QUALITY_FILE = ROOT / "data" / "data-quality.json"


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def split_times(value):
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def latest_date(items):
    dates = [str(item.get("lastDate", "")) for item in items if item.get("lastDate")]
    return max(dates) if dates else None


def source_status(snapshot, quality=None):
    yahoo = list((snapshot.get("yahooSymbols") or {}).values())
    naver = list((snapshot.get("naverSymbols") or {}).values())
    fred = list((snapshot.get("fredSeries") or {}).values())
    market_indexes = list((snapshot.get("naverMarketIndexes") or {}).values())
    live_market_indexes = sum(item.get("fetchStatus") == "live" for item in market_indexes)

    sources = [
        {
            "id": "yahoo",
            "label": "Yahoo Finance",
            "status": "ok" if yahoo else "error",
            "lastDate": latest_date(yahoo),
            "seriesCount": len(yahoo),
            "detail": f"주가지수·환율·글로벌 자산 {len(yahoo)}개 시계열",
        },
        {
            "id": "naver-equity",
            "label": "Naver 국내주식",
            "status": "ok" if naver else "error",
            "lastDate": latest_date(naver),
            "seriesCount": len(naver),
            "detail": f"국내주식·ETF {len(naver)}개 시계열",
        },
        {
            "id": "naver-market-index",
            "label": "Naver 시장지표",
            "status": "ok" if market_indexes and live_market_indexes == len(market_indexes) else "warning",
            "lastDate": latest_date(market_indexes),
            "seriesCount": len(market_indexes),
            "detail": f"운임·원자재·금리·환율 {len(market_indexes)}개 중 실시간 조회 {live_market_indexes}개",
        },
        {
            "id": "fred",
            "label": "FRED",
            "status": "ok" if fred else "error",
            "lastDate": latest_date(fred),
            "seriesCount": len(fred),
            "detail": f"금리·크레딧·금융여건 {len(fred)}개 시계열 · 저장값 대체 경로 포함",
        },
    ]
    quality_groups = {item.get("id"): item for item in (quality or {}).get("sourceGroups", [])}
    for source in sources:
        group = quality_groups.get(source["id"])
        if not group:
            continue
        source.update(
            {
                "status": group.get("status", source["status"]),
                "lastDate": group.get("latestLastDate") or source["lastDate"],
                "oldestLastDate": group.get("oldestLastDate"),
                "freshCount": group.get("freshCount"),
                "staleCount": group.get("staleCount"),
                "fallbackCount": group.get("fallbackCount"),
                "detail": group.get("detail", source["detail"]),
            }
        )
    known_ids = {source["id"] for source in sources}
    for group_id, group in quality_groups.items():
        if group_id in known_ids:
            continue
        sources.append(
            {
                "id": group_id,
                "label": group.get("label", group_id),
                "status": group.get("status", "warning"),
                "lastDate": group.get("latestLastDate"),
                "oldestLastDate": group.get("oldestLastDate"),
                "seriesCount": group.get("seriesPresent"),
                "freshCount": group.get("freshCount"),
                "staleCount": group.get("staleCount"),
                "fallbackCount": group.get("fallbackCount"),
                "detail": group.get("detail", ""),
            }
        )
    return sources


def artifact_status(data):
    dashboard = data["dashboard"]
    definitions = [
        ("dashboard", "시장리스크", (dashboard.get("metadata") or {}).get("generatedAt")),
        ("ml", "ML 위험신호", data["ml"].get("generatedAt")),
        ("els", "ELS 지수위험", data["els"].get("generatedAt")),
        ("hmm", "HMM 레짐", data["hmm"].get("generatedAt")),
        ("backtest", "시장 백테스트", data["backtest"].get("generatedAt")),
        ("stress", "스트레스 이력", data["stress"].get("generatedAt")),
        ("quality", "데이터 완비성", data["quality"].get("generatedAt")),
    ]
    return [
        {"id": item_id, "label": label, "status": "ok" if generated_at else "warning", "generatedAt": generated_at}
        for item_id, label, generated_at in definitions
    ]


def stage_status(args):
    return [
        {
            "id": "market",
            "label": "시장데이터·지표",
            "status": "success",
            "durationSeconds": args.market_duration,
            "detail": "시장 데이터 수집, 위험점수, ELS·HMM 산출",
        },
        {
            "id": "ml",
            "label": "ML 신호",
            "status": "success",
            "durationSeconds": args.ml_duration,
            "detail": "특성 생성, 모델 학습, 예측 및 OOS 검증",
        },
        {
            "id": "validation",
            "label": "품질검증",
            "status": "success",
            "durationSeconds": args.validation_duration,
            "detail": "대시보드 계약·정렬·가독성·캐시 검증",
        },
        {
            "id": "deployment",
            "label": "Git·Pages 배포",
            "status": "success",
            "durationSeconds": None,
            "detail": "공개된 상태 파일은 Git 푸시와 Pages 배포가 완료된 실행입니다.",
        },
    ]


def build_payload(args):
    output = Path(args.output)
    previous = read_json(output)
    data = {
        "dashboard": read_json(ROOT / "data" / "risk-dashboard.json"),
        "snapshot": read_json(ROOT / "data" / "market-risk-snapshot.json"),
        "ml": read_json(ROOT / "data" / "ml-risk-signal.json"),
        "els": read_json(ROOT / "data" / "els-index-risk.json"),
        "hmm": read_json(ROOT / "data" / "hmm-regime.json"),
        "backtest": read_json(ROOT / "data" / "market-risk-backtest.json"),
        "stress": read_json(ROOT / "data" / "market-stress-episodes.json"),
        "quality": read_json(DATA_QUALITY_FILE),
    }
    dashboard_metadata = data["dashboard"].get("metadata") or {}
    schedule_times = split_times(args.times)
    saturday_times = split_times(args.saturday_times)
    full_times = set(split_times(args.full_times))
    trigger = "scheduled" if args.scheduled_time else "manual"
    scheduled_time = args.scheduled_time or None
    completed_key = args.completed_at.replace(" KST", "").replace("-", "").replace(":", "").replace(" ", "T")
    run_id = args.run_id or f"{completed_key}-{scheduled_time or 'manual'}"
    current = {
        "runId": run_id,
        "status": "success",
        "mode": args.mode,
        "trigger": trigger,
        "scheduledTime": scheduled_time,
        "startedAt": args.started_at,
        "completedAt": args.completed_at,
        "durationSeconds": args.total_duration,
        "dataAsOf": dashboard_metadata.get("asOf"),
        "dashboardGeneratedAt": dashboard_metadata.get("generatedAt"),
        "mlGeneratedAt": data["ml"].get("generatedAt"),
        "qualityScore": data["quality"].get("score"),
        "message": (
            "데이터 생성과 완비성 검증을 통과했습니다."
            if data["quality"].get("status") == "ok"
            else "데이터 생성은 완료됐으며 일부 완비성 항목을 확인해야 합니다."
        ),
    }
    history = [current]
    history.extend(item for item in previous.get("history", []) if item.get("runId") != run_id)

    return {
        "schemaVersion": 1,
        "generatedAt": args.completed_at,
        "current": current,
        "schedule": {
            "timezone": "Asia/Seoul",
            "weekdaysOnly": False,
            "times": [
                {"time": item, "mode": "full" if item in full_times else "fast"}
                for item in schedule_times
            ],
            "saturdayTimes": [{"time": item, "mode": "full"} for item in saturday_times],
            "expectedDurationMinutes": {"fast": 5, "full": 25},
            "delayGraceMinutes": 5,
        },
        "stages": stage_status(args),
        "sources": source_status(data["snapshot"], data["quality"]),
        "artifacts": artifact_status(data),
        "quality": {
            "status": data["quality"].get("status", "warning"),
            "score": data["quality"].get("score"),
            "referenceDate": data["quality"].get("referenceDate"),
            "summary": data["quality"].get("summary") or {},
            "issues": (data["quality"].get("issues") or [])[:8],
        },
        "history": history[:12],
    }


def parse_args():
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    parser = argparse.ArgumentParser(description="홈페이지 운영현황 상태 파일을 생성합니다.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=["fast", "full"], default="full")
    parser.add_argument("--times", default="07:30,12:30,15:35")
    parser.add_argument("--saturday-times", default="07:30")
    parser.add_argument("--full-times", default="07:30,15:35")
    parser.add_argument("--scheduled-time", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--started-at", default=now)
    parser.add_argument("--completed-at", default=now)
    parser.add_argument("--total-duration", type=int, default=0)
    parser.add_argument("--market-duration", type=int, default=0)
    parser.add_argument("--ml-duration", type=int, default=0)
    parser.add_argument("--validation-duration", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(args)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote pipeline status: {output}")


if __name__ == "__main__":
    main()

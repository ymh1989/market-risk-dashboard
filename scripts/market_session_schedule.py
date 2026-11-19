"""특별 거래일의 마감 연장을 실행기·감시기·화면에서 함께 적용한다."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import xml.etree.ElementTree as ET

KST = timezone(timedelta(hours=9))
# 연도까지 지정한다. 수능일을 매년 같은 날짜로 추정하지 않는다.
SESSION_EXCEPTIONS = {
    "2026-11-19": {
        "reason": "수능일 장 마감 1시간 순연",
        "market": {"15:35": "16:35", "18:30": "19:30"},
        "news": {"16:10": "17:10"},
        "eodReadyTime": "16:35",
    }
}


def shifted_time(value: str, day: date, kind: str = "market") -> str:
    """확인된 특별 거래일에만 예약 시각을 변경한다."""
    changes = SESSION_EXCEPTIONS.get(day.isoformat(), {}).get(kind, {})
    return changes.get(value, value)


def original_time(value: str, day: date) -> str:
    """순연된 예약의 원래 시각을 찾아 full/krx 실행 모드를 유지한다."""
    changes = SESSION_EXCEPTIONS.get(day.isoformat(), {}).get("market", {})
    return {new: old for old, new in changes.items()}.get(value, value)


def shifted_times(values: str, day: date, kind: str = "market") -> str:
    """기존 예약 목록에만 예외를 적용하며 같은 시각은 중복하지 않는다."""
    return ",".join(sorted({
        shifted_time(value.strip(), day, kind)
        for value in values.split(",") if value.strip()
    }))


def breadth_reference_date(now: datetime) -> date:
    """특별 거래일에도 장 마감 전에는 전일 EOD를 요청한다."""
    current = now.astimezone(KST)
    ready = SESSION_EXCEPTIONS.get(current.date().isoformat(), {}).get("eodReadyTime", "15:35")
    return (current if current.strftime("%H:%M") >= ready else current - timedelta(days=1)).date()


def schedule_overrides(schedule: dict) -> dict:
    """정규 일정을 보존하면서 날짜별 화면용 예외를 생성한다."""
    overrides = {}
    for key, exception in SESSION_EXCEPTIONS.items():
        day = date.fromisoformat(key)
        weekday = day.weekday()
        slots = (schedule.get("mondayTimes", schedule["times"]) if weekday == 0
                 else schedule["times"] if weekday < 5
                 else schedule.get("saturdayTimes", []) if weekday == 5 else [])
        overrides[key] = {
            "reason": exception["reason"],
            "times": sorted([
                {**slot, "time": shifted_time(slot["time"], day)} for slot in slots
            ], key=lambda slot: slot["time"]),
        }
    return overrides


def extra_launchd_intervals(times: str, monday_times: str, saturday_times: str, today: date) -> str:
    """아직 지나지 않은 특별 거래일의 추가 기동 시각을 만든다."""
    entries = []
    for key in SESSION_EXCEPTIONS:
        day = date.fromisoformat(key)
        if day < today or day.weekday() == 6:
            continue
        raw = monday_times if day.weekday() == 0 else saturday_times if day.weekday() == 5 else times
        for value in sorted(set(raw.split(","))):
            shifted = shifted_time(value.strip(), day)
            if shifted == value.strip():
                continue
            hour, minute = map(int, shifted.split(":"))
            entry = ET.Element("dict")
            for name, number in (("Month", day.month), ("Day", day.day), ("Hour", hour), ("Minute", minute)):
                ET.SubElement(entry, "key").text = name
                ET.SubElement(entry, "integer").text = str(number)
            entries.append(ET.tostring(entry, encoding="unicode"))
    return "\n".join(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=datetime.now(KST).date().isoformat())
    parser.add_argument("--times", default="")
    parser.add_argument("--original-time")
    parser.add_argument("--breadth-date", action="store_true")
    parser.add_argument("--launchd-xml", action="store_true")
    parser.add_argument("--monday-times", default="")
    parser.add_argument("--saturday-times", default="")
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    if args.breadth_date:
        print(breadth_reference_date(datetime.now(KST)).isoformat())
    elif args.launchd_xml:
        print(extra_launchd_intervals(args.times, args.monday_times, args.saturday_times, day))
    elif args.original_time is not None:
        print(original_time(args.original_time, day))
    else:
        print(shifted_times(args.times, day))


if __name__ == "__main__":
    main()

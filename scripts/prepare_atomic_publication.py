from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
MANIFEST_PATH = Path("data/publication-manifest.json")
JSON_ARTIFACTS = (
    Path("data/risk-dashboard.json"),
    Path("data/market-risk-snapshot.json"),
    Path("data/market-risk-timeseries.json"),
    Path("data/naver-marketindex-history.json"),
    Path("data/market-risk-backtest.json"),
    Path("data/market-stress-episodes.json"),
    Path("data/market-history-cache.json"),
    Path("data/els-index-risk.json"),
    Path("data/hmm-regime.json"),
    Path("data/ml-risk-signal.json"),
    Path("data/data-quality.json"),
    Path("data/pipeline-status.json"),
    Path("data/m7-credit-proxy.json"),
    Path("data/kospi-breadth.json"),
)
STATIC_ARTIFACTS = (Path("reports/market-risk-dashboard-offline.html"),)
DEFAULT_ARTIFACTS = JSON_ARTIFACTS + STATIC_ARTIFACTS
REQUIRED_PIPELINE_STATUS = Path("data/pipeline-status.json")
REQUIRED_QUALITY_STATUS = Path("data/data-quality.json")


class PublicationError(RuntimeError):
    """게시 후보가 하나의 검증된 스냅샷을 구성하지 못할 때 발생한다."""


def _json_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PublicationError(f"필수 게시 파일이 없습니다: {path}") from error
    except json.JSONDecodeError as error:
        raise PublicationError(f"JSON 형식이 손상됐습니다: {path} ({error})") from error
    if not isinstance(payload, dict) or not payload:
        raise PublicationError(f"게시 JSON 최상위 객체가 비어 있습니다: {path}")
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(" KST"):
        text = text[:-4] + "+09:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _generated_at(payload: dict[str, Any]) -> str | None:
    value = payload.get("generatedAt") or (payload.get("metadata") or {}).get("generatedAt")
    return str(value) if value else None


def _observation_date(payload: dict[str, Any]) -> str | None:
    containers = [
        payload,
        payload.get("metadata") or {},
        payload.get("latest") or {},
        payload.get("period") or {},
        payload.get("current") or {},
        payload.get("source") or {},
    ]
    keys = (
        "asOf",
        "date",
        "lastDate",
        "endDate",
        "sampleEnd",
        "dataAsOf",
        "trainingEndDate",
        "referenceDate",
    )
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if not value:
                continue
            text = str(value)[:10]
            try:
                return date.fromisoformat(text).isoformat()
            except ValueError:
                continue
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
    result = []
    for value in paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise PublicationError(f"게시 파일은 프로젝트 내부 상대경로여야 합니다: {value}")
        result.append(path)
    if len(result) != len(set(result)):
        raise PublicationError("게시 파일 목록에 중복 경로가 있습니다.")
    return tuple(result)


def _validate_source_times(
    payloads: dict[Path, dict[str, Any]],
    generated_paths: set[Path],
    started_at: datetime,
    prepared_at: datetime,
) -> None:
    earliest_allowed = started_at - timedelta(minutes=2)
    latest_allowed = prepared_at + timedelta(minutes=5)
    for path in generated_paths:
        payload = payloads[path]
        generated_text = _generated_at(payload)
        generated_at = _parse_datetime(generated_text)
        if generated_at is None:
            raise PublicationError(f"생성시각을 확인할 수 없습니다: {path}")
        if generated_at < earliest_allowed:
            raise PublicationError(
                f"이번 실행의 신규 산출물이 아닙니다: {path} · 생성 {generated_text} · 시작 {started_at.isoformat()}"
            )
        if generated_at > latest_allowed:
            raise PublicationError(f"생성시각이 현재보다 미래입니다: {path} · {generated_text}")


def _validate_control_files(payloads: dict[Path, dict[str, Any]], run_id: str) -> None:
    pipeline = payloads.get(REQUIRED_PIPELINE_STATUS)
    if pipeline is not None:
        pipeline_run_id = str((pipeline.get("current") or {}).get("runId") or "")
        if pipeline_run_id != run_id:
            raise PublicationError(
                f"pipeline runId가 게시 runId와 다릅니다: {pipeline_run_id or '없음'} != {run_id}"
            )
        if (pipeline.get("current") or {}).get("status") != "success":
            raise PublicationError("pipeline-status의 현재 실행이 성공 상태가 아닙니다.")

    quality = payloads.get(REQUIRED_QUALITY_STATUS)
    if quality is not None and quality.get("status") == "error":
        raise PublicationError("데이터 완비성 판정이 error라 게시를 중단합니다.")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify_publication(
    root: str | Path,
    *,
    manifest_path: str | Path = MANIFEST_PATH,
    expected_run_id: str | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    manifest_relative = _relative_paths([manifest_path])[0]
    manifest = _json_payload(root_path / manifest_relative)
    run_id = str(manifest.get("runId") or "")
    if manifest.get("status") != "ready" or not run_id:
        raise PublicationError("publication manifest가 게시 준비 완료 상태가 아닙니다.")
    if expected_run_id and run_id != expected_run_id:
        raise PublicationError(f"manifest runId가 예상과 다릅니다: {run_id} != {expected_run_id}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise PublicationError("manifest에 게시 파일 목록이 없습니다.")
    if int(manifest.get("artifactCount") or 0) != len(artifacts):
        raise PublicationError("manifest의 게시 파일 개수가 실제 목록과 다릅니다.")

    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise PublicationError("manifest 게시 파일 항목의 형식이 올바르지 않습니다.")
        relative_text = str(artifact.get("path") or "")
        if not relative_text:
            raise PublicationError("manifest 게시 경로가 비어 있습니다.")
        relative = _relative_paths([relative_text])[0]
        if relative in seen:
            raise PublicationError("manifest 게시 경로가 비어 있거나 중복됐습니다.")
        seen.add(relative)
        path = root_path / relative
        if not path.exists():
            raise PublicationError(f"manifest 게시 파일이 없습니다: {relative}")
        if path.stat().st_size != int(artifact.get("bytes") or -1):
            raise PublicationError(f"게시 파일 크기가 manifest와 다릅니다: {relative}")
        if _sha256(path) != artifact.get("sha256"):
            raise PublicationError(f"게시 파일 체크섬이 manifest와 다릅니다: {relative}")
        if relative.suffix == ".json":
            payload = _json_payload(path)
            publication = payload.get("publication") or {}
            if publication.get("runId") != run_id:
                raise PublicationError(f"게시 JSON의 runId가 manifest와 다릅니다: {relative}")

    return manifest


def prepare_publication(
    root: str | Path,
    *,
    run_id: str,
    mode: str,
    started_at: str,
    artifact_paths: Iterable[str | Path] = DEFAULT_ARTIFACTS,
    reused_paths: Iterable[str | Path] = (),
    manifest_path: str | Path = MANIFEST_PATH,
    prepared_at: datetime | None = None,
) -> dict[str, Any]:
    if not run_id.strip():
        raise PublicationError("게시 runId가 비어 있습니다.")
    if mode not in {"fast", "full"}:
        raise PublicationError(f"지원하지 않는 갱신 모드입니다: {mode}")
    started = _parse_datetime(started_at)
    if started is None:
        raise PublicationError(f"실행 시작시각을 해석할 수 없습니다: {started_at}")
    prepared = (prepared_at or datetime.now(KST)).astimezone(KST)
    if prepared < started:
        raise PublicationError("게시 준비시각이 실행 시작시각보다 빠릅니다.")

    root_path = Path(root)
    artifacts = _relative_paths(artifact_paths)
    reused = set(_relative_paths(reused_paths))
    manifest_relative = _relative_paths([manifest_path])[0]
    if manifest_relative in artifacts:
        raise PublicationError("publication manifest는 게시 산출물 목록과 분리해야 합니다.")
    unknown_reused = reused - set(artifacts)
    if unknown_reused:
        raise PublicationError(f"재사용 파일이 게시 목록에 없습니다: {sorted(map(str, unknown_reused))}")

    payloads: dict[Path, dict[str, Any]] = {}
    for relative in artifacts:
        source = root_path / relative
        if not source.exists():
            raise PublicationError(f"필수 게시 파일이 없습니다: {relative}")
        if source.stat().st_size <= 0:
            raise PublicationError(f"필수 게시 파일이 비어 있습니다: {relative}")
        if relative.suffix == ".json":
            payloads[relative] = _json_payload(source)

    _validate_control_files(payloads, run_id)
    generated_json = set(payloads) - reused
    _validate_source_times(payloads, generated_json, started, prepared)

    prepared_text = prepared.strftime("%Y-%m-%d %H:%M:%S KST")
    started_text = started.strftime("%Y-%m-%d %H:%M:%S KST")
    with tempfile.TemporaryDirectory(prefix=".publication-", dir=root_path) as temporary:
        staging_root = Path(temporary)
        artifact_rows = []
        for relative in artifacts:
            source = root_path / relative
            staged = staging_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            state = "reused" if relative in reused else "generated"
            if relative.suffix == ".json":
                payload = payloads[relative]
                payload["publication"] = {
                    "schemaVersion": 1,
                    "runId": run_id,
                    "mode": mode,
                    "state": state,
                    "startedAt": started_text,
                    "preparedAt": prepared_text,
                }
                _write_json(staged, payload)
            else:
                shutil.copy2(source, staged)
            artifact_rows.append(
                {
                    "path": str(relative),
                    "type": "json" if relative.suffix == ".json" else "html",
                    "state": state,
                    "generatedAt": _generated_at(payloads[relative]) if relative in payloads else None,
                    "observationDate": _observation_date(payloads[relative]) if relative in payloads else None,
                    "bytes": staged.stat().st_size,
                    "sha256": _sha256(staged),
                }
            )

        manifest = {
            "schemaVersion": 1,
            "status": "ready",
            "runId": run_id,
            "mode": mode,
            "startedAt": started_text,
            "preparedAt": prepared_text,
            "commitAtomic": True,
            "artifactCount": len(artifact_rows),
            "generatedCount": sum(item["state"] == "generated" for item in artifact_rows),
            "reusedCount": sum(item["state"] == "reused" for item in artifact_rows),
            "checks": {
                "requiredFilesPresent": True,
                "jsonParsed": True,
                "pipelineRunIdMatched": REQUIRED_PIPELINE_STATUS not in payloads
                or (payloads[REQUIRED_PIPELINE_STATUS].get("current") or {}).get("runId") == run_id,
                "sourceTimesValid": True,
                "allJsonStamped": True,
                "checksumsVerified": True,
            },
            "artifacts": artifact_rows,
        }
        _write_json(staging_root / manifest_relative, manifest)
        verify_publication(staging_root, manifest_path=manifest_relative, expected_run_id=run_id)

        for relative in artifacts:
            destination = root_path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_root / relative, destination)
        destination_manifest = root_path / manifest_relative
        destination_manifest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_root / manifest_relative, destination_manifest)

    return verify_publication(root_path, manifest_path=manifest_relative, expected_run_id=run_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="대시보드 게시 파일을 하나의 검증된 runId로 묶습니다.")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mode", choices=["fast", "full"], default="full")
    parser.add_argument("--started-at", default="")
    parser.add_argument("--reused-file", action="append", default=[])
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--expected-run-id", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.verify_only:
            manifest = verify_publication(
                args.root,
                manifest_path=args.manifest,
                expected_run_id=args.expected_run_id or None,
            )
        else:
            manifest = prepare_publication(
                args.root,
                run_id=args.run_id,
                mode=args.mode,
                started_at=args.started_at,
                reused_paths=args.reused_file,
                manifest_path=args.manifest,
            )
    except PublicationError as error:
        raise SystemExit(f"원자적 게시 준비 실패: {error}") from None
    action = "검증 완료" if args.verify_only else "준비 완료"
    print(
        f"원자적 게시 {action}: {manifest['runId']} · "
        f"신규 {manifest['generatedCount']}개 · 재사용 {manifest['reusedCount']}개"
    )


if __name__ == "__main__":
    main()

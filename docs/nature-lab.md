# 창밖 · 자연 파노라마

기존 Field Lab과 조형물 화면의 나무 아이콘으로 진입하는 별도 감상 화면.
실사 영상을 사용하며 생성형 3D 자연 시뮬레이션이나 실시간 현장 중계가 아님.

## 감상

- 산과 물 사이, 숲이 깨어나는 시간, 알프스의 능선 등 세 장면
- 자동 재생은 무음, 기본 속도 0.75배. 0.5배·1배 선택 가능
- 반복 구간과 장면 변경은 두 영상의 교차 전환으로 연결. 짧은 원본의 반복 경계가 완전히 사라지는 것은 아님
- 마우스에 따라 화면이 소폭 이동. 360도 촬영이나 자유로운 3D 카메라는 아님
- 움직임이 없으면 조작부 숨김. 마우스·터치·키보드 입력으로 복귀
- 풍경 순환은 선택사항. 약 1분 이상 머문 뒤 반복 경계에서 다음 장면으로 전환
- 전체 화면, 일시정지, 재생 속도 선택 지원. 소리 없음

## 성능과 접근성

- 시장 데이터·점수·ML·정기 배치에 영향 없음
- 대시보드 첫 화면에서 영상 다운로드·디코딩 없음
- 선택한 장면만 로딩. 교차 전환 중에만 최대 두 영상을 동시 재생
- 탭을 숨기면 영상과 애니메이션 정지. 사용자가 일시정지한 상태는 복귀 후에도 유지
- 운영체제의 동작 줄이기 설정에서는 정지 상태로 시작, 마우스 이동 효과 없음
- 자동 재생 차단이나 영상 오류가 발생하면 정지 화면과 재생·재시도 제공
- 외부 플레이어·추적 스크립트·CDN 요청 없음. 저장소에 포함된 로컬 미디어 사용
- `nature-lab.html`과 `src/media/nature/`가 함께 있어야 오프라인 감상 가능. 기존 시장 대시보드의 단일 HTML 내보내기에는 포함하지 않음

## 원본과 이용 조건

영상은 Mixkit Stock Video Free License. 공식 페이지의 무료 라이선스를 확인한 후 사용.
이 화면의 일부로 사용하며 별도의 원본 영상 배포 서비스가 아님.

| 장면 | 공식 원본 | 로컬 파일 |
| --- | --- | --- |
| 산과 물 사이 | [Turquoise blue water bay from above / 5008](https://mixkit.co/free-stock-video/turquoise-blue-water-bay-from-above-5008/) | `water.mp4`, `water.jpg` |
| 숲이 깨어나는 시간 | [Slow aerial tour through a mist-covered forest / 28342](https://mixkit.co/free-stock-video/slow-aerial-tour-through-a-mist-covered-forest-28342/) | `forest.mp4`, `forest.jpg` |
| 알프스의 능선 | [Mountainous area in the Alps / 4132](https://mixkit.co/free-stock-video/mountainous-area-in-the-alps-4132/) | `alps.mp4`, `alps.jpg` |

- [영상 라이선스](https://mixkit.co/license/#videoFree), [라이선스 상세](https://mixkit.co/license/modal/videoFree/)
- 앞부분 0.8초를 제외한 13초 사용. 1080p, H.264, CRF 22, faststart, 오디오 제외
- 각 영상의 1초 지점에서 포스터 추출. 영상 합계 약 16MB
- 아이콘: 기존 Lucide 자산과 `trees` 아이콘. ISC 라이선스는 `src/vendor/lucide.LICENSE`에 보존

## 개발과 검증

```sh
python scripts/build_nature_lab.py
python scripts/build_chamber_lab.py
python -m pytest -q tests/test_nature_lab.py tests/test_chamber_lab.py
python tests/nature_browser_check.py
```

브라우저 검증은 Playwright와 Pillow 필요. `NATURE_BROWSER`로 검증용 Chromium 실행 파일을 지정할 수 있음.
`nature-lab.html`은 빌드 결과이며 코드 수정은 `src/nature-lab.html`, `src/nature-lab.css`, `src/nature-lab.js`에서 수행.

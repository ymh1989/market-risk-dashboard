---
title: 통합 리스크 모니터링 대시보드
sdk: static
app_file: index.html
---

# 통합 리스크 모니터링 대시보드

시장리스크와 ELS 발행·헤지 환경을 함께 점검하는 정적 대시보드입니다. 시장 종합점수에 반영되는 18개 가중지표와 연구용 관찰지표 2개, 스트레스 사례, KOSPI ML risk-off 신호, SPX·SX5E·NKY·HSCEI·KOSPI200 ELS 리스크를 한 화면에서 확인할 수 있습니다. 별도 프런트엔드 빌드 도구 없이 `index.html`, `src/*`, `data/*.json`으로 실행되며, 신용리스크와 유동성리스크를 같은 구조로 확장할 수 있습니다.

운영 페이지: [https://ymh1989.github.io/market-risk-dashboard/](https://ymh1989.github.io/market-risk-dashboard/)

## 실행

```bash
make serve
```

브라우저에서 `http://localhost:5173`을 열면 됩니다.

## 테스트

```bash
make test
pytest -q
```

`make test`는 대시보드 데이터 스키마, 시장리스크 점수 계산과 화면용 산출물 존재 여부를 확인합니다. `pytest -q`는 ML feature·target 정렬, 미래정보 누수 방지, walk-forward 분할, 전처리 학습 범위와 재현성을 검증합니다.

## 시장리스크 데이터 갱신

```bash
make update-market-risk
make backtest-market-risk
make analyze-stress-episodes
make export-els-index-risk
```

이 명령들은 Yahoo Finance, Naver Finance 주식·시장지표 엔드포인트, FRED에서 데이터를 받아 시장리스크 점수, 감사용 스냅샷, 최근 시계열, 백테스트, 과거 스트레스 사례와 ELS 기초지수별 리스크를 갱신합니다. 현재 모델은 한국 시장지표, 수급·거래량, 글로벌 크레딧·위험선호, 미국 신용스프레드·금융여건, 운임·원자재·국제환율, AI 반도체 및 빅테크 AI 수요 지표를 함께 사용합니다.

- 한국 시장: KOSPI, KOSDAQ, USD/KRW
- 글로벌 스트레스: VIX, 미국 10년 금리 proxy
- 수급·거래량: 삼성전자, SK하이닉스, 한미반도체, KODEX 200의 거래량 및 외국인소진율
- AI 반도체 공급망: SOX, NVIDIA, TSMC ADR, Broadcom, AMD, Micron, ASML, 삼성전자, SK하이닉스, 한미반도체, DB하이텍, 리노공업
- 빅테크 AI 수요: Apple, Microsoft, Alphabet, Meta, Amazon의 가격 스트레스와 메모리 공급사 대비 수익률 격차
- 단일종목 레버리지: 삼성전자·SK하이닉스의 KOSPI 대비 상대 변동성과 단기 가격 스트레스
- 글로벌 proxy: HYG/LQD 신용스프레드 proxy, EEM 신흥국 위험선호 proxy
- 교차자산 전이: SCFI·BDTI 비용압력과 BDI 실물수요의 괴리, 브렌트유의 원화 환산 비용, USD/CNY·철광석을 결합한 중국 경기 압력
- 보조 원자재: 구리/금 상대가격을 중국 경기 카드의 상세값으로 제공하되 별도 점수는 부여하지 않습니다.
- 연구 관찰카드: USD/JPY·VIX·SPX의 엔 캐리 청산 압력과 한국 3년-미국 2년 금리차·USD/KRW의 원화 압력을 0~100점으로 표시합니다. 두 카드는 가중치 0으로 종합점수·위험군 점수·고위험 지표 수에서 제외합니다.

점수는 최대 2년 히스토리 기준의 레벨, 20개 관측치 변화율, 20일 실현변동성, 252일 고점대비 낙폭을 세 방식으로 표준화한 뒤 가중평균합니다. 주간 SCFI는 4개 관측치 변화를 사용합니다. 첫째는 과거 분포 내 분위수 순위, 둘째는 `z = (현재값 - 평균) / 표준편차`를 정규분포 CDF로 0~100 변환한 값, 셋째는 median/MAD 기반 robust z-score 변환값입니다. 현재 모델은 분위수 40%, z-score 30%, robust z-score 30%로 섞습니다. 운영 환경에서는 동일한 스크립트 구조에서 Yahoo/Naver provider를 KRX, 한국은행 ECOS, 금융투자협회, 내부 외국인 수급/포지션 데이터 provider로 교체하면 됩니다.

지표는 Crash Stress, Overheating, Liquidity, Flow, Macro, AI Semi 하위 리스크 그룹으로 나뉘며, 각 지표와 그룹의 최종 점수 기여도를 함께 저장합니다. Macro 그룹에는 VIX·환율·금리 proxy, FRED 하이일드 OAS 기반 미국 신용스프레드, 미국 금융여건 긴축 압력과 함께 해상운임 비용 충격, 중국 경기·위안화 압력, 원화 환산 에너지 수입비용을 포함합니다. 서로 다른 휴장일과 주기를 가진 시계열은 해당 날짜까지 공개된 직전 값만 사용해 결합합니다. `make backtest-market-risk`는 최근 점수 시계열을 KOSPI 향후 20거래일 최대낙폭과 비교해 진단 결과를 저장하고, `make analyze-stress-episodes`는 과거 고점수 구간의 실제 낙폭과 주요 기여지표를 정리합니다.

관찰카드는 최소 한 번의 시계열 안전 OOS 비교에서 기존 점수의 하락 탐지력이나 오경보율을 개선한 경우에만 운영 가중치에 편입합니다. 편입 시에는 전체 가중치를 늘리지 않고 기존 Macro 그룹 안에서 중복되는 환율·변동성·금융여건 비중을 재배분합니다.

갱신 시 `data/market-risk-timeseries.json`도 함께 생성됩니다. 이 파일은 각 시장리스크 지표의 최근 120개 관측치 기준 0~100 점수 흐름을 담고, 홈페이지의 지표 카드 안에서 작은 시계열 차트로 표시됩니다.

## 로컬 예약 갱신

GitHub Actions의 `schedule` 트리거는 트래픽과 큐 상태에 따라 정해진 시각보다 늦게 실행될 수 있습니다. 정시성이 중요한 운영 갱신은 macOS `launchd`로 로컬 맥에서 실행하고, 결과 JSON만 `main`으로 push하는 방식을 기본으로 사용합니다.

먼저 `.env.example`을 `.env`로 복사하고 아래 값을 확인합니다.

```bash
LOCAL_MARKET_UPDATE_TIMES=08:30,12:30,15:35
LOCAL_MARKET_UPDATE_REMOTE=origin
LOCAL_MARKET_UPDATE_BRANCH=main
```

설치 명령은 아래와 같습니다.

```bash
make install-local-market-update
```

설치 후 LaunchAgent는 지정된 분마다 스크립트를 깨우고, 스크립트가 KST 기준 평일 `08:30`, `12:30`, `15:35`일 때만 실제 갱신을 수행합니다. 현재 작업 폴더에 README나 설정 파일 변경이 남아 있어도 예약 작업이 막히지 않도록, 스크립트는 `origin/main` 기준의 깨끗한 임시 worktree에서 데이터 갱신, ML 재학습, 테스트, JSON 커밋·푸시를 처리합니다.

수동으로 같은 갱신을 실행하려면 아래 명령을 사용합니다.

```bash
make run-local-market-update
```

로그는 `logs/local-market-update.log`, 오류 로그는 `logs/local-market-update.err.log`에 저장됩니다. 맥이 잠자기 상태이거나 네트워크/SSH 인증이 불가능하면 해당 시각 갱신은 실패할 수 있으므로 운영 장비는 예약 시각에 깨어 있고 GitHub push 권한이 있어야 합니다.

## 운영현황 콘솔

홈페이지 상단 상태 바와 `운영현황` 탭은 `data/pipeline-status.json`을 읽습니다. 마지막 성공시각, 다음 예약, 데이터 기준일, 시장지표·ML·검증·배포 단계, Yahoo·Naver·FRED 최신 관측일과 최근 성공 이력을 한 화면에서 확인할 수 있습니다.

예약시각이 됐지만 해당 실행의 새 완료 기록이 아직 없으면 예상 소요시간 동안 `갱신 중`으로 표시합니다. fast는 5분, full은 25분을 기본 예상시간으로 사용하며 여기에 5분의 유예시간을 더한 뒤에도 완료 기록이 없으면 `지연`으로 바뀝니다. GitHub Pages는 정적 홈페이지이므로 운영현황은 읽기 전용이며, 수동 재실행은 `make run-local-market-update` 또는 `bash scripts/run_local_market_update.sh --fast`로 수행합니다.

`scripts/write_pipeline_status.py`는 성공한 예약 실행마다 기존 이력을 최대 12건까지 유지합니다. 실패한 작업은 새 상태 파일을 배포하지 못하더라도 브라우저가 마지막 성공 기록과 현재 예약시각을 비교해 지연으로 판정합니다.

## ELS 5개 기초지수 리스크

`make export-els-index-risk`는 ELS에서 주로 사용하는 SPX, SX5E, NKY, HSCEI, KOSPI200을 각각 0~100점으로 평가합니다. 지수별 점수는 20일 실현변동성 분위수 35%, 252일 고점 대비 낙폭 22%, 20일 하락 모멘텀 18%, 고변동성 상승 과열 15%, 60일 낙폭 6%, 최근 일간 충격 4%로 합성합니다.

Basket 점수는 worst-of 구조를 고려해 가장 위험한 지수 50%, 두 번째 지수 20%, 5개 지수 평균 15%, 지수 간 상관도 15%를 반영합니다. 높은 점수는 투자자에게 낙인 접근 위험이 커졌다는 뜻인 동시에, 증권사 관점에서는 발행 조건 개선 가능성과 기존 북의 순연·헤지 비용 증가 우려가 함께 커졌다는 의미입니다.

## ELS 발행기회·헤지부담 맵

홈페이지의 `ELS 발행·헤지` 탭은 위의 단일 리스크 점수를 증권사 관점의 두 축으로 분해합니다. 가로축 `상대 발행기회`는 252일 변동성 분위수 55%, 20일 변동성 수준 30%, 20일/60일 변동성 충격 15%를 반영합니다. 세로축 `헤지부담`은 20일 하락 모멘텀 25%, 252일 고점 대비 낙폭 25%, 20일 변동성 수준 25%, 최근 일간 충격 15%, 지수 동조화 10%를 반영합니다.

판단 구간은 `발행기회`, `선별발행`, `헤지주의`, `발행부담` 네 단계입니다. 변동성이 높아 발행 조건이 개선될 가능성이 있어도 낙폭과 하락 경로가 동시에 크면 기존 북의 순연과 감마·베가 부담을 우선하도록 `헤지주의` 또는 `발행부담`으로 표시합니다.

맵의 `1개월`과 `3개월` 보기는 각 기초지수가 과거 위치에서 현재 위치까지 이동한 궤적을 같은 좌표계에 표시합니다. 과거 점수는 해당 날짜까지 관측된 가격, 실현변동성, 낙폭과 지수 간 상관만 사용하며, 빈 원은 조회 기간의 시작점이고 채운 원은 현재 위치입니다.

이 도구는 공개 종가지수의 실현변동성과 낙폭을 이용한 상대평가이며 실제 쿠폰을 추정하지 않습니다. 실제 발행 의사결정에는 만기별 내재변동성, skew·상관 smile, 금리·배당·조달비용, 기발행 재고와 상품별 delta·gamma·vega를 추가해 확인해야 합니다.

## ML Risk-off 패널

ML 패널은 최근 10년 시장 데이터를 다시 수집해 leakage-safe feature, expanding walk-forward 검증, 모델 학습과 최신 예측을 순서대로 수행한 결과입니다. Risk-off 확률은 단순한 약세 확률이 아니라 향후 20영업일의 하락, 낙폭과 고변동성 위험을 함께 반영합니다. 따라서 지수가 상승 중이어도 실현변동성이 높으면 `고변동성 활황`으로 표시되며 확률이 높게 나올 수 있습니다.

모델 아티팩트는 로컬 예약 갱신 또는 수동 GitHub Actions 실행 때마다 다시 생성되고 `data/ml-risk-signal.json`으로 내보냅니다. 대시보드는 ML과 기준모델의 macro F1, risk-off 재현율·정밀도·AUC·Brier score를 함께 표시해 성능 차이를 확인할 수 있게 합니다.

모델링 방식은 한국은행의 FSI/FVI처럼 여러 금융안정 관련 지표를 표준화해 종합지수로 합성하는 접근을 참고했습니다. 자세한 배경은 한국은행의 [FSI와 FVI 설명](https://www.bok.or.kr/portal/bbs/B0000347/view.do?menuNo=201106&nttId=10077975&pageIndex=1)을 참고하면 됩니다.

## Transformer·앙상블 실험실

`ensemble-lab`은 운영 모델과 분리된 challenger 실험입니다. 향후 5거래일 안의 -5%, -10% 급락을 대상으로 RF, Transformer Encoder, 현재 모멘텀 rule과 두 가지 앙상블을 같은 expanding walk-forward 구간에서 비교합니다. 타깃의 미래 5일과 테스트 구간이 겹치지 않도록 각 학습 fold 말단 5일을 제거하며, imputer·scaler·Spearman 피처 선택은 fold의 학습 데이터에만 적합합니다.

Transformer는 MPS·CUDA·CPU를 지원하고 여러 random seed의 확률을 평균할 수 있습니다. `attention`, `last`, `mean` pooling과 BCE·focal loss, 사인 위치 인코딩을 비교할 수 있지만 기본값은 재현 실험에서 더 안정적이었던 위치 인코딩 미사용입니다. 적응 앙상블 가중치는 충분한 이전 OOS fold와 이벤트가 쌓이기 전에는 고정값을 유지합니다.

```bash
PYTHONPATH=src python3 -m kospi_risk.cli ensemble-lab \
  --features data/processed/features.parquet \
  --config configs/base.yaml \
  --max-folds 24 --device mps --seed-count 2 \
  --feature-selection spearman --max-features 40 \
  --moderate-pooling attention --severe-pooling last \
  --report-output reports/research/transformer_ensemble_lab_v2.md
```

결과 보고서는 AP, AUC, Brier, 상위 10% 적중률·이벤트 포착률과 RF 대비 fold 승률을 함께 보여줍니다. 같은 OOS 구간에서 여러 구조를 비교한 선택 편향이 있으므로 운영 대시보드에는 자동 반영하지 않으며, 신규 forward 데이터로 별도 승격 기준을 통과해야 합니다.

## 운영 데이터 소스 전환 후보

현재 구현은 키 없이 테스트 가능한 Yahoo/Naver proxy 중심입니다. 운영 정확도를 높일 때는 아래 공식 데이터로 provider를 교체하는 것이 좋습니다.

- KRX: PER/PBR, 투자자별 매매동향, 공매도, 파생상품 basis
- 한국은행 ECOS: 원/달러 환율, CD/CP/국고채 금리, 금융시장 통계
- 금융투자협회 KOFIA: 회사채 AA-/BBB- 금리, CD/CP 최종호가수익률, 신용융자/예탁금
- 내부 데이터: 포트폴리오 민감도, 외국인·기관 수급, 리스크 한도 소진율

## 텔레그램 리스크 뉴스 브리핑

KB증권, 시장리스크, 사모펀드, 고객 투자상품 리스크 관련 뉴스를 Google News RSS에서 모아 텔레그램으로 발송할 수 있습니다.

1. 텔레그램에서 BotFather로 봇을 만들고 토큰을 받습니다.
2. 봇에게 메시지를 한 번 보낸 뒤, `https://api.telegram.org/bot<토큰>/getUpdates`에서 `chat.id`를 확인합니다.
3. `.env.example`을 `.env`로 복사하고 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 채웁니다.
4. 먼저 미리보기로 기사 수집 결과를 확인합니다.

```bash
python3 scripts/send_risk_news_digest.py --dry-run
```

5. 텔레그램 발송을 테스트합니다.

```bash
make send-news-digest
```

6. macOS LaunchAgent에 매일 08:30 KST 실행으로 등록합니다.

```bash
make install-news-digest
```

발송 시간은 `.env` 또는 실행 환경에서 `NEWS_DIGEST_TIMES=08:30,12:30,15:30`처럼 쉼표로 구분해 바꿀 수 있습니다. 예약 실행은 launchd가 매시 지정 분에 스크립트를 깨우고, 스크립트가 KST 기준 목표 시각일 때만 발송하도록 보호합니다. 키워드는 `config/news_digest_keywords.json`에서 조정합니다. 팀 단체방과 개인방처럼 여러 곳에 동시에 보내려면 `TELEGRAM_CHAT_IDS`에 쉼표로 구분해 추가합니다.

기본값으로 `NEWS_DIGEST_KOREAN_WEB_ONLY=true`가 적용되어 한글 제목 기사만 남기고, 러시아권 도메인이나 키릴 문자가 포함된 원천 매체는 제외합니다.

텔레그램에서 사용자가 직접 호출하려면 polling 봇을 실행합니다.

```bash
make run-news-bot
```

봇 대화창이나 팀 단체방에서 `/news`, `/latest`, `/risk`를 보내면 즉시 최신 브리핑을 답장합니다. 기본적으로 `.env`의 `TELEGRAM_CHAT_ID`, `TELEGRAM_CHAT_IDS`와 일치하는 채팅에서만 반응합니다. 팀 단체방에 봇을 초대하고 그 방에서 `/chatid`를 보내 chat id를 확인한 뒤 `TELEGRAM_CHAT_ID` 또는 `TELEGRAM_CHAT_IDS`에 추가하면, 정시 공지와 사용자 호출을 같은 방에서 함께 쓸 수 있습니다.

## 유지보수 구조

- `data/risk-dashboard.json`: 기준일, 탭, 리스크 섹션, 지표, 운영 기준을 관리합니다.
- `data/market-risk-snapshot.json`: 외부 데이터 갱신 시점의 원천 티커와 산출 지표 스냅샷입니다.
- `data/market-risk-timeseries.json`: 지표별 최근 점수 시계열입니다.
- `data/market-risk-backtest.json`: 최근 점수 구간별 KOSPI 향후 최대낙폭 진단 결과입니다.
- `data/market-stress-episodes.json`: 과거 고위험 구간의 낙폭과 주요 기여지표를 저장합니다.
- `data/market-history-cache.json`: 스트레스 사례 재현에 필요한 시장 히스토리 캐시입니다.
- `data/naver-marketindex-history.json`: 네이버 운임·금속·에너지·채권·국제환율의 선별 원천 이력과 자산별 실시간/캐시 사용 상태를 저장합니다.
- `data/els-index-risk.json`: ELS 5개 기초지수와 worst-of basket 리스크를 저장합니다.
- `data/ml-risk-signal.json`: 최신 ML risk-off 신호, 성능지표와 최근 흐름을 저장합니다.
- `data/pipeline-status.json`: 예약 스케줄, 최근 성공, 단계별 소요시간, 데이터 소스 신선도와 실행 이력을 저장합니다.
- `src/risk-model.js`: 점수 계산과 등급 판정 로직입니다.
- `src/app.js`: JSON 데이터를 읽어 화면을 렌더링합니다.
- `src/styles.css`: 대시보드 레이아웃과 시각 스타일입니다.
- `scripts/update_market_risk.py`: 외부 데이터를 가져와 시장리스크 지표를 재계산합니다.
- `scripts/export_els_index_risk.py`: ELS 5개 기초지수 및 basket 리스크를 계산합니다.
- `scripts/export_ml_risk_signal.py`: 연구용 ML 결과를 홈페이지용 JSON으로 변환합니다.
- `scripts/write_pipeline_status.py`: 홈페이지 운영현황 상태 파일을 생성하고 최근 성공 이력을 누적합니다.
- `scripts/backtest_market_risk.py`: 시장 종합점수의 선행 낙폭 진단을 생성합니다.
- `scripts/analyze_stress_episodes.py`: 과거 스트레스 구간과 기여지표를 분석합니다.
- `scripts/send_risk_news_digest.py`: 키워드별 최신 뉴스를 수집해 텔레그램 브리핑을 발송합니다.

## 신용/유동성 리스크 추가 방법

1. `data/risk-dashboard.json`에서 `credit` 또는 `liquidity` 탭의 `enabled` 값을 `true`로 바꿉니다.
2. 같은 id를 가진 section의 `status`를 `active`로 바꿉니다.
3. `indicators` 배열에 다음 형태로 지표를 추가합니다.

```json
{
  "id": "credit_spread",
  "name": "신용스프레드 확대",
  "category": "시장가격",
  "value": 62,
  "unit": "score",
  "weight": 0.25,
  "trend": "up",
  "detail": "회사채 AA- 3년 스프레드 3개월 변화율",
  "source": "Credit spread export"
}
```

## 외부 접속 배포

이 프로젝트는 정적 사이트라서 아래 중 하나로 바로 공개할 수 있습니다.

- Hugging Face Spaces: 새 Space를 `Static` 타입으로 만들고 이 폴더를 push합니다.
- GitHub Pages: 저장소에 push한 뒤 Pages source를 root로 지정합니다.
- Netlify/Vercel: 빌드 명령 없이 publish directory를 프로젝트 루트로 지정합니다.

실제 운영에서는 로컬 예약 갱신기가 화면에서 읽는 `data/*.json`을 갱신하고 commit·push하면, 화면 코드 수정 없이 최신 대시보드를 발행할 수 있습니다. GitHub Actions는 예약 실행이 아니라 수동 백업 실행용으로 남겨 둡니다.

## KOSPI 리스크 Regime Lab

이 저장소에는 `kospi-risk-regime-lab` Python 패키지도 포함되어 있습니다. 한국 주가지수 리스크 관리와 ELS 발행/헤지 환경 점검을 위한 연구용 ML 파이프라인이며, 임의 매매 신호가 아니라 의사결정 보조 지표 생성을 목표로 합니다.

### 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

### 입력 데이터

기본 입력 파일은 `data/raw/market_data.csv`입니다. 필수 컬럼은 `date`, `KOSPI`, `SPX`, `SOX`, `USDKRW`입니다. `VIX`, `VKOSPI`, `NASDAQ`, 수급, basis, skew, 금리, 원자재 등 선택 컬럼은 존재할 때만 feature로 사용하며, 없어도 파이프라인은 실패하지 않습니다.

실제 시장 데이터 소스는 `configs/data_sources.yaml`에서 관리합니다. 현재 기본 구현은 Yahoo Finance chart API를 사용해 KOSPI, S&P 500, SOX, USD/KRW와 일부 글로벌 optional 지표를 수집합니다. 수집 감사 정보는 `data/raw/market_data_sources.json`에 저장됩니다.

Backtest는 기본적으로 최근 12개 walk-forward fold를 사용합니다. 더 긴 검증을 원하면 `configs/base.yaml`의 `validation.max_backtest_folds`를 늘리고, 전체 기간 fold를 모두 돌리고 싶으면 `0`으로 바꿉니다.

### 실행 순서

```bash
python -m kospi_risk.cli fetch-market-data --source-config configs/data_sources.yaml --output data/raw/market_data.csv --metadata data/raw/market_data_sources.json --min-rows 1500
python -m kospi_risk.cli build-features --input data/raw/market_data.csv --output data/processed/features.parquet --config configs/base.yaml
python -m kospi_risk.cli train --features data/processed/features.parquet --config configs/base.yaml
python -m kospi_risk.cli backtest --features data/processed/features.parquet --config configs/base.yaml --output reports/backtest_report.md
python -m kospi_risk.cli predict-latest --features data/processed/features.parquet --config configs/base.yaml --output reports/latest_signal.csv
python scripts/export_ml_risk_signal.py
```

샘플 데이터로 빠르게 구조만 확인하려면 아래 명령을 대신 사용합니다.

```bash
python -m kospi_risk.cli make-sample-data
```

산출물은 `reports/backtest_report.md`, `reports/latest_signal.csv`, `reports/score_bucket_analysis.csv`, `reports/model_metrics.csv`, `models/model_bundle.joblib`입니다.

### 출력 해석

`latest_signal.csv`는 20영업일 KOSPI 실현변동성 예측치, `risk-on / neutral / risk-off` regime 확률, KOSPI의 S&P 500 및 SOX 대비 20영업일 초과성과 확률, ELS 리스크 점수와 구간을 제공합니다. ELS 점수는 0~30 낮음, 30~60 정상 모니터링, 60~80 상승 리스크, 80~100 스트레스 구간으로 해석합니다. 시각화 파일은 `reports/figures/`에 PNG로 저장됩니다.

### 누수 방지와 한계

Feature는 각 날짜 `t`까지 관측 가능한 rolling return, volatility, correlation, drawdown, optional market 변수만 사용합니다. Target은 `t+1`부터 `t+20`까지의 미래 구간만 사용하며 마지막 20행 target은 학습에서 제외됩니다. 검증은 random split 없이 expanding walk-forward 방식으로 수행합니다. 첫 버전은 해석 가능성과 재현성을 우선한 baseline/Ridge/RandomForest 중심 구현이며, 운영 전에는 KRX, ECOS, KOFIA, 내부 수급/파생 포지션 데이터로 원천을 교체하고 모델 모니터링, feature drift, 휴장일 캘린더, 모델 승인 절차를 추가해야 합니다.

### 테스트

```bash
pytest -q
```

### GitHub Pages 배포

1. GitHub에서 새 repository를 만듭니다. 예: `market-risk-dashboard`
2. 로컬에서 이 프로젝트를 Git 저장소로 초기화하고 push합니다.

```bash
git init
git add .
git commit -m "Initial risk dashboard"
git branch -M main
git remote add origin https://github.com/<username>/<repo>.git
git push -u origin main
```

3. GitHub repository에서 `Settings` → `Pages`로 이동합니다.
4. `Build and deployment`에서 `Deploy from a branch`를 선택합니다.
5. Branch는 `main`, folder는 `/root`로 저장합니다.

배포 URL은 보통 아래 형태입니다.

```text
https://<username>.github.io/<repo>/
```

정시 갱신은 `make install-local-market-update`로 설치한 로컬 LaunchAgent가 담당합니다. `.github/workflows/update-market-risk.yml`은 `workflow_dispatch`만 남겨 두었으므로, GitHub Actions 탭에서 필요할 때 수동으로 같은 갱신을 실행하는 백업 경로로 사용할 수 있습니다.

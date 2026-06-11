---
title: Integrated Risk Monitoring Dashboard
sdk: static
app_file: index.html
---

# Integrated Risk Monitoring Dashboard

시장리스크를 먼저 운영하고, 이후 신용리스크와 유동성리스크를 같은 구조로 추가할 수 있게 만든 정적 대시보드입니다. 별도 빌드 도구 없이 `index.html`, `src/*`, `data/*.json`만으로 실행됩니다.

## 실행

```bash
make serve
```

브라우저에서 `http://localhost:5173`을 열면 됩니다.

## 테스트

```bash
make test
```

테스트는 대시보드 데이터 스키마, 시장리스크 점수 계산, 향후 확장 모듈 존재 여부를 확인합니다.

## 시장리스크 데이터 갱신

```bash
make update-market-risk
make backtest-market-risk
```

이 명령은 Yahoo Finance와 Naver Finance chart 엔드포인트에서 2년 일별 데이터를 받아와 `data/risk-dashboard.json`의 시장리스크 지표와 `data/market-risk-snapshot.json` 감사용 스냅샷을 갱신합니다. 현재 모델은 한국 시장지표, 수급/거래량, 글로벌 크레딧/위험선호, AI 반도체 모니터링 지표를 함께 사용합니다.

- 한국 시장: KOSPI, KOSDAQ, USD/KRW
- 글로벌 스트레스: VIX, 미국 10년 금리 proxy
- 수급/거래량: 삼성전자, SK하이닉스, 한미반도체, KODEX 200, KODEX 레버리지의 거래량 및 외국인소진율
- AI 반도체: SOX, NVIDIA, TSMC ADR, Broadcom, AMD, Micron, ASML, 삼성전자, SK하이닉스, 한미반도체, DB하이텍, 리노공업
- 글로벌 proxy: HYG/LQD 신용스프레드 proxy, EEM 신흥국 위험선호 proxy

점수는 2년 히스토리 기준의 레벨, 20일 변화율, 20일 실현변동성, 252일 고점대비 낙폭을 세 방식으로 표준화한 뒤 가중평균합니다. 첫째는 과거 분포 내 분위수 순위, 둘째는 `z = (현재값 - 평균) / 표준편차`를 정규분포 CDF로 0~100 변환한 값, 셋째는 median/MAD 기반 robust z-score 변환값입니다. 현재 모델은 분위수 40%, z-score 30%, robust z-score 30%로 섞습니다. 운영 환경에서는 동일한 스크립트 구조에서 Yahoo/Naver provider를 KRX, 한국은행 ECOS, 금융투자협회, 내부 외국인 수급/포지션 데이터 provider로 교체하면 됩니다.

지표는 Crash Stress, Overheating, Liquidity, Flow, Macro, AI Semi 하위 리스크 그룹으로 나뉘며, 각 지표와 그룹의 최종 점수 기여도를 함께 저장합니다. `make backtest-market-risk`는 최근 점수 시계열을 KOSPI 향후 20거래일 최대낙폭과 비교해 간단한 진단 결과를 `data/market-risk-backtest.json`에 저장합니다.

갱신 시 `data/market-risk-timeseries.json`도 함께 생성됩니다. 이 파일은 각 시장리스크 지표의 최근 120개 관측치 기준 0~100 점수 흐름을 담고, 홈페이지의 지표 카드 안에서 작은 시계열 차트로 표시됩니다.

모델링 방식은 한국은행의 FSI/FVI처럼 여러 금융안정 관련 지표를 표준화해 종합지수로 합성하는 접근을 참고했습니다. 자세한 배경은 한국은행의 [FSI와 FVI 설명](https://www.bok.or.kr/portal/bbs/B0000347/view.do?menuNo=201106&nttId=10077975&pageIndex=1)을 참고하면 됩니다.

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

발송 시간은 `.env` 또는 실행 환경에서 `NEWS_DIGEST_HOUR`, `NEWS_DIGEST_MINUTE`로 바꿀 수 있습니다. 키워드는 `config/news_digest_keywords.json`에서 조정합니다.

## 유지보수 구조

- `data/risk-dashboard.json`: 기준일, 탭, 리스크 섹션, 지표, 운영 기준을 관리합니다.
- `data/market-risk-snapshot.json`: 외부 데이터 갱신 시점의 원천 티커와 산출 지표 스냅샷입니다.
- `data/market-risk-timeseries.json`: 지표별 최근 점수 시계열입니다.
- `data/market-risk-backtest.json`: 최근 점수 구간별 KOSPI 향후 최대낙폭 진단 결과입니다.
- `src/risk-model.js`: 점수 계산과 등급 판정 로직입니다.
- `src/app.js`: JSON 데이터를 읽어 화면을 렌더링합니다.
- `src/styles.css`: 대시보드 레이아웃과 시각 스타일입니다.
- `scripts/update_market_risk.py`: 외부 데이터를 가져와 시장리스크 지표를 재계산합니다.
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

실제 운영에서는 데이터 생성 파이프라인이 `data/risk-dashboard.json`만 갱신하도록 만들면 화면 코드 수정 없이 매일 최신 대시보드를 발행할 수 있습니다.

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

`.github/workflows/update-market-risk.yml`은 평일 16:10 KST에 시장리스크 데이터를 자동 갱신하고, 변경된 `data/*.json`을 다시 commit합니다. GitHub Actions 탭에서 수동 실행도 가능합니다.

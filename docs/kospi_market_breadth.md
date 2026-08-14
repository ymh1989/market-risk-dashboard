# KOSPI Market Breadth 운영 가이드

## 목적

KOSPI 지수 방향과 시장 내부의 상승·하락 종목 확산 정도를 함께 봅니다.

- 지수 상승 + VKOSPI 하락 + breadth 확산: 건강한 risk-on 후보
- 지수 상승 + breadth 약화: 일부 대형주 중심 상승 후보
- 지수 하락 + VKOSPI 상승 + breadth 급락: systemic risk·panic 후보
- 삼성전자·SK하이닉스 약세 + KOSPI·breadth 견조 + VKOSPI 하락: sector rotation 후보

관찰 플래그는 확정 판정이 아니라 검토 대상을 좁히는 용도입니다.

## 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
```

필수 패키지는 `pykrx`, `pandas`, `numpy`, `pyarrow`, `matplotlib`입니다. 최신 pykrx는 Python 3.10 이상이 필요합니다.

이번 기능의 일자별 KOSPI 전 종목 조회는 KRX 로그인 대상입니다. [KRX 정보데이터시스템](https://data.krx.co.kr/) 계정의 ID와 비밀번호를 로컬 `.env`에 입력합니다.

```dotenv
KRX_ID=본인_KRX_ID
KRX_PW=본인_KRX_비밀번호
```

CLI는 저장소 루트의 `.env`를 자동으로 읽되 이미 설정된 환경변수를 덮어쓰지 않습니다. `.env`는 `.gitignore` 대상이며 비밀번호를 명령행 인자로 전달하거나 저장소에 커밋하지 않습니다. 자격증명이 없으면 날짜별 재시도 전에 즉시 종료합니다.

- [pykrx PyPI](https://pypi.org/project/pykrx/)
- [pykrx GitHub](https://github.com/sharebook-kr/pykrx)

## 실행

최초 실행은 시작일을 지정합니다.

```bash
PYTHONPATH=src python3 -m kospi_risk.cli update-kospi-breadth \
  --start 2024-01-01 \
  --end 2026-08-11 \
  --output data/processed/kospi_breadth.parquet \
  --metadata data/quality/kospi_breadth_update.json \
  --raw-dir data/raw/kospi_breadth \
  --figures reports/figures/kospi_breadth
```

다음 실행부터는 기존 파일의 마지막 날짜 다음 영업일부터 조회합니다.

```bash
PYTHONPATH=src python3 -m kospi_risk.cli update-kospi-breadth \
  --output data/processed/kospi_breadth.parquet
```

실패일을 복구하거나 과거 원천을 재조회할 때만 `--start YYYY-MM-DD --refresh-from-start`를 함께 사용합니다. 해당 시작일부터 다시 호출한 뒤 날짜 중복을 제거하고 새 값을 보존합니다.

외국인·기관 순매수는 기간을 한 번에 조회하고, 프로그램 순매수는 KRX 화면 특성상 거래일별로 조회합니다. KRX 호출 제한을 고려해 프로그램 최초 적재는 최근 80거래일로 제한하고 최신일부터 채우며, 이후에는 누락된 새 거래일만 추가합니다. 세 날짜가 연속 실패하면 해당 실행의 프로그램 조회를 중단하고 기존 값을 보존합니다. 원천 장애를 점검할 때 전체 직접 수급은 `--skip-flows`, 프로그램만은 `--skip-program-flows`로 생략할 수 있습니다.

`make update-kospi-breadth`도 같은 명령을 실행합니다. 최초 시작일은 `KOSPI_BREADTH_START`로 바꿀 수 있습니다.

```bash
make update-kospi-breadth KOSPI_BREADTH_START=2025-01-01
```

## VKOSPI 결합

현재 프로젝트에는 검증된 일별 VKOSPI 원천이 없습니다. KOSPI200 HMM은 현재 20일 실현변동성을 대체값으로 사용합니다. breadth 모듈은 VKOSPI를 임의 생성하거나 실현변동성으로 바꾸지 않습니다.

다음 컬럼 중 하나를 가진 CSV 또는 Parquet만 선택적으로 결합합니다.

- 날짜: `date`
- 값: `VKOSPI`, `vkospi`, `vkospi_close`, `close`

```bash
PYTHONPATH=src python3 -m kospi_risk.cli update-kospi-breadth \
  --output data/processed/kospi_breadth.parquet \
  --vkospi data/raw/vkospi_daily.csv
```

VKOSPI 파일을 생략하면 결과에 `vkospi`, `vkospi_change` 컬럼과 세 번째 차트를 만들지 않습니다.

## 산출 컬럼

| 컬럼 | 산식·의미 |
|---|---|
| `up` | 등락률이 0보다 큰 유효 종목 수 |
| `down` | 등락률이 0보다 작은 유효 종목 수 |
| `flat` | 등락률이 0인 유효 종목 수 |
| `total` | `up + down + flat` |
| `net_breadth` | `up - down` |
| `ad_ratio` | `up / down`, 분모 0이면 NaN |
| `breadth_pct` | `(up - down) / (up + down)` |
| `up_ratio` | `up / (up + down)` |
| `down_ratio` | `down / (up + down)` |
| `AD_line` | 저장된 첫 관측일부터 `net_breadth` 누적합 |
| `AD_ma5`, `AD_ma20` | AD Line 5·20거래일 이동평균 |
| `breadth_ma5`, `breadth_ma20` | breadth 비율 5·20거래일 이동평균 |
| `samsung_return`, `hynix_return` | 당일 전 종목 응답에서 읽은 삼성전자·SK하이닉스 수익률 |
| `foreign_net_buy_value` | 외국인·기타외국인 합산 순매수 거래대금, 원 |
| `institution_net_buy_value` | 금융투자·보험·투신·사모·은행·기타금융·연기금 합산 순매수 거래대금, 원 |
| `financial_investment_net_buy_value`, `pension_net_buy_value` | 금융투자·연기금 당일 순매수 거래대금, 원 |
| `program_net_buy_value` | 차익·비차익 프로그램 전체 순매수 거래대금, 원 |
| `*_net_buy_5d` | 해당 투자자의 5거래일 순매수 거래대금 누계 |
| `*_sell_pressure` | 5일 순매도 강도의 직전 최대 252거래일 내 분위수, 0~100 |
| `direct_flow_pressure` | 외국인 45%·기관 35%·프로그램 20% 매도압력 합성점수 |

AD Line의 절대값은 저장 시작일에 종속됩니다. 서로 다른 시작일로 만든 파일의 AD Line 수준을 직접 비교하지 않고, 같은 파일 안의 방향·고점·저점·지수와의 다이버전스를 봅니다.

추가 관찰 플래그는 아래 기준을 사용합니다.

- `healthy_risk_on`: KOSPI 수익률 양수, VKOSPI 하락, breadth 양수이면서 전일보다 개선
- `narrow_rally`: KOSPI 수익률 양수, breadth 음수
- `panic`: KOSPI 수익률 음수, VKOSPI 상승, breadth -0.4 이하
- `sector_rotation`: 삼성전자·SK하이닉스 동반 약세, KOSPI·breadth 견조, VKOSPI 하락

삼성전자·SK하이닉스 수익률은 같은 날 받은 KOSPI 전 종목 응답에서 직접 읽습니다. 두 티커가 없거나 VKOSPI를 결합하지 않으면 `sector_rotation`은 `False`이며 임의 값을 만들지 않습니다.

직접 수급 압력은 각 날짜까지 존재하는 값만 사용합니다. 5거래일 순매수 누계를 음수 방향으로 바꾼 뒤 직전 최대 252거래일에서의 분위수를 계산하며 최소 60개 관측이 쌓이기 전에는 값을 만들지 않습니다. 프로그램 매매는 독립 투자자군이 아니라 주문 방식이므로 외국인·기관 거래와 일부 중첩될 수 있습니다. 프로그램 값이 일시 누락되면 외국인·기관 가중치만 다시 비례 조정하지만, 원천 상태는 `not_available` 또는 경고로 별도 표시합니다. 이 보조 진단은 기존 종합점수와 6개 그룹 가중치를 변경하지 않습니다.

## 저장 산출물

- 운영 데이터: `data/processed/kospi_breadth.parquet`
- 선택 원자료: `data/raw/kospi_breadth/YYYY-MM-DD.parquet`
- 품질·실패 로그: `data/quality/kospi_breadth_update.json`
- Chart 1: `reports/figures/kospi_breadth/kospi_breadth_pct.png`
- Chart 2: `reports/figures/kospi_breadth/kospi_ad_line.png`
- Chart 3: `reports/figures/kospi_breadth/kospi_vkospi_breadth.png`, VKOSPI가 있을 때만 생성

메타데이터에는 요청 중 실패한 날짜, 휴장·빈 응답 날짜, 최근 10거래일 universe 범위, 투자자·프로그램 순매수 상태와 VKOSPI 결합 여부를 기록합니다. 특정 날짜 조회가 실패해도 나머지 날짜는 계속 처리합니다. 최초 실행에서 모든 거래일이 실패하면 빈 파일을 만들지 않고 종료합니다.

## 품질검사와 수동 sanity check

최근 10거래일은 다음을 자동 확인합니다.

- `up + down + flat == total`
- 전일 대비 `total` 12% 초과 급변 여부
- 최근 중앙값의 75% 미만으로 universe가 축소됐는지 여부

한 날짜를 KRX 또는 네이버금융의 KOSPI 상승·보합·하락 종목 수와 비교할 때는 장 마감 후 같은 기준시각을 사용합니다.

```python
from kospi_risk.data_loader import load_frame
from kospi_risk.market_breadth import sanity_check_breadth

frame = load_frame("data/processed/kospi_breadth.parquet")
result = sanity_check_breadth(
    frame,
    "2026-08-10",
    reference_counts={"up": 0, "down": 0, "flat": 0},  # 화면에서 확인한 값으로 교체
)
print(result)
```

`reference_counts`의 0은 예시 자리표시자입니다. 실제 비교 때 화면값으로 바꿉니다.

pykrx `stock.get_market_ohlcv(date, market="KOSPI")`가 반환한 index가 당일 집계 universe입니다. 아래처럼 원자료를 직접 확인할 수 있습니다.

```python
from pykrx import stock

raw = stock.get_market_ohlcv("20260810", market="KOSPI")
print(raw.shape)
print(raw[["종가", "등락률"]].head())
```

KRX·네이버 화면과 차이가 나는 주요 원인은 다음과 같습니다.

- 장중 화면과 확정 종가의 기준시각 차이
- 우선주·SPAC·REIT 등 종목 유형의 포함 규칙
- 거래정지 종목과 신규상장 종목의 등락률 처리
- ETF·ETN을 주식 universe와 별도로 집계하는 화면 규칙
- pykrx 버전과 KRX 원천 응답의 종목 분류 변화

기본 universe는 pykrx의 KOSPI 주식 응답입니다. KOSPI에 상장된 우선주·SPAC·REIT는 응답에 포함될 수 있지만, ETF·ETN은 별도 전용 API 대상이므로 기본 집계에 넣지 않습니다. 선택 원자료의 `ticker` 목록과 화면의 포함 규칙을 먼저 맞춘 뒤 숫자를 비교합니다. ETF·ETN breadth가 필요하면 전용 universe를 분리 수집해 이름이 다른 지표로 관리합니다.

## 자동 갱신

증분 갱신이 기본이므로 cron이나 기존 macOS `launchd`에서 같은 명령을 반복 실행할 수 있습니다. 한국 장 마감 데이터가 안정된 뒤 실행하는 예시는 아래와 같습니다.

```cron
10 16 * * 1-5 cd /path/to/market-lab && PYTHONPATH=src python3 -m kospi_risk.cli update-kospi-breadth --output data/processed/kospi_breadth.parquet >> logs/kospi-breadth.log 2>&1
```

휴장일은 빈 응답으로 기록하고 저장 행을 추가하지 않습니다. 장 마감 직후 원천이 아직 갱신되지 않은 경우를 고려해 재시도와 날짜별 실패 로그를 유지합니다.

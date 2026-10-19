# 조달·실질금리 관찰카드

## 운영 범위
- 신규 카드 3개: 국내 기업 조달경색, 미국 실질금리·할인율 부담, 미국 단기 달러조달 압력
- 유럽: 기존 프랑스·독일 국채금리차 카드에 프랑스 은행주 상대 흐름 추가
- 전부 가중치 0. 기존 종합점수, 그룹점수, ML, HMM 및 ELS 산식 변경 없음
- AI 기업 분기 재무지표는 미연결

## 원천과 계산
| 구분 | 원천 | 계산·해석 |
|---|---|---|
| 국내 기업 | 기존 KB IVA10370 `ratesPct` | CP91일-CD91일, 회사채 AA급3년-국고채3년. 같은 날짜 금리만 차감하고 %p를 100배해 bp 표시 |
| 실질금리 | FRED DFII10, T10YIE, DGS10 | 실질금리만 점수화. 기대인플레이션·명목금리는 공통일 보조 판독. 기대인플레이션에는 위험·유동성 프리미엄도 포함 |
| 달러 단기조달 | FRED SOFR, IORB, SOFR99. SOFR 계열 원천은 뉴욕연은 | SOFR-IORB 및 SOFR99-SOFR(bp). 각 5개 공통 관측일 후행 평균을 점수화해 50:50 결합 |
| 유럽 은행 | Yahoo BNP.PA, GLE.PA, ACA.PA, EXW1.DE | EUR 배당·분할 조정가격. 은행 3개를 매일 동일가중한 수익률을 누적하고 EURO STOXX 50 추종 ETF와 비교 |

- 유럽 상대지수 = (은행 바스켓 누적지수 / ETF 누적지수) * 100. 최초 공통일 100
- 은행주 5일·20일 상대수익률 = 은행 기간수익률 - ETF 기간수익률(%p)
- 은행 상대 흐름은 기존 국채금리차 점수에 합산하지 않음. 은행 CDS나 신용사건 발생확률이 아님
- 은행·ETF 통화가 EUR가 아니거나 조정가격이 없으면 사용하지 않음. 유럽 현지 18시 전 당일 봉 제외
- 공통 관측일만 결합. 국가별 휴일·금리 발표시차를 전일값 채움으로 숨기지 않음

## 점수와 이력
- 최소 120개 원천 관측 이전에는 점수 대신 원자료 카드 표시. 없는 점수를 0으로 만들지 않음
- 기존 혼합 정규화 재사용: 분위수 40%, z-score 정규누적분포 30%, median/MAD robust z 30%
- 구성점수: 수준 55%(당일까지 최대 504개), 20일 양의 확대폭 45%(당일까지 최대 252개)
- 국내 금리차 각각 50%. 달러 단기조달은 5일 후행 평균에 같은 산식을 적용
- 절대적인 위기 확률이 아닌 과거 분포 대비 관찰점수. 점수 하락과 원값 하락은 다를 수 있음
- 달러조달 월말·분기말 효과를 통계적으로 제거한 모델은 아님. 5일 평균은 단발성 급등 완충 목적
- 국내 금리 이력은 잔고 원장의 5년 이력과 다름. 도입 시 실제 금리 관측은 24개이며 별도 과거 보강 전 긴 이력처럼 표시하지 않음
- 배당 조정가격 및 FRED는 사후 수정 가능. 이력은 당시 공개본을 복원한 투자전략 백테스트가 아님

## 갱신·실패·공유
- 전체·빠른 갱신에서 `update_market_risk.py`의 기존 계산 뒤 관찰 모듈 실행
- 관찰만 갱신: `python3 scripts/update_market_risk.py --refresh-observations-only`
- FRED 기존 키·공개 CSV 경로 재사용. 원천 3개 동시 조회로 호출 수 제한
- 원천별 실패는 보관값 사용 여부와 기준일 표시. 보관값도 없으면 미수집 표시, 기존 가중 배치는 계속 진행
- 원장: `data/market-history-cache.json`의 `marketWatchSources`. 새 게시 파일 추가 없이 기존 원자적 게시에 포함
- 카드·출처: 기존 dashboard/snapshot. 점수 흐름: 기존 timeseries. 짧은 이력은 `rawObservationCards`로 분리
- 1M/3M/YTD/1Y/3Y 공통 기간과 마우스 날짜·값 조회 연동. 오프라인 HTML에도 동일 자료 포함
- 화면 생성시각, 원천 조회시각, 실제 관측일을 구분

## 공식 설명
- [미국 실질금리](https://fred.stlouisfed.org/series/DFII10)
- [기대인플레이션](https://fred.stlouisfed.org/series/T10YIE)
- [SOFR 99분위](https://fred.stlouisfed.org/series/SOFR99)
- [연준 준비금 이자율](https://fred.stlouisfed.org/series/IORB)
- [EURO STOXX 50 ETF](https://www.ishares.com/de/professionelle-anleger/de/produkte/251783/ishares-euro-stoxx-50-ucits-etf-de-fund)

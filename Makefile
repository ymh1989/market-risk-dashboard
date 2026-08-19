.PHONY: serve test audit-data update-m7-credit-proxy update-market-risk update-kospi-breadth backtest-market-risk analyze-stress-episodes export-offline send-news-digest install-news-digest run-local-market-update install-local-market-update run-overnight-market-prepare install-overnight-market-prepare

KOSPI_BREADTH_START ?= 2024-01-01

serve:
	python3 -m http.server 5173 --bind 127.0.0.1

test:
	python3 tests/smoke_test.py

audit-data:
	python3 scripts/audit_data_completeness.py --strict

update-m7-credit-proxy:
	python3 -m m7_credit_proxy.pipeline --update-latest

update-market-risk:
	python3 scripts/update_market_risk.py

update-kospi-breadth:
	PYTHONPATH=src .venv-breadth/bin/python -m kospi_risk.cli update-kospi-breadth --start $(KOSPI_BREADTH_START)

backtest-market-risk:
	python3 scripts/backtest_market_risk.py

analyze-stress-episodes:
	python3 scripts/analyze_stress_episodes.py

export-offline:
	python3 scripts/export_offline_dashboard.py

send-news-digest:
	python3 scripts/send_risk_news_digest.py

install-news-digest:
	bash scripts/install_news_digest_launchd.sh

run-local-market-update:
	bash scripts/run_local_market_update.sh

install-local-market-update:
	bash scripts/install_local_market_update_launchd.sh

run-overnight-market-prepare:
	bash scripts/run_overnight_market_prepare.sh

install-overnight-market-prepare:
	bash scripts/install_overnight_market_prepare_launchd.sh

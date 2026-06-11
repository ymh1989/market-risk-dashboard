.PHONY: serve test update-market-risk backtest-market-risk analyze-stress-episodes send-news-digest install-news-digest

serve:
	python3 -m http.server 5173 --bind 127.0.0.1

test:
	python3 tests/smoke_test.py

update-market-risk:
	python3 scripts/update_market_risk.py

backtest-market-risk:
	python3 scripts/backtest_market_risk.py

analyze-stress-episodes:
	python3 scripts/analyze_stress_episodes.py

send-news-digest:
	python3 scripts/send_risk_news_digest.py

install-news-digest:
	bash scripts/install_news_digest_launchd.sh

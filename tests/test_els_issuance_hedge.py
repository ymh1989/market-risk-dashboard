import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def load_els_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "export_els_index_risk.py"
    spec = importlib.util.spec_from_file_location("export_els_index_risk", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_asset(asset_id, label, asset_type="index", stressed=False):
    return {
        "id": asset_id,
        "label": label,
        "name": f"{label} test asset",
        "region": "테스트",
        "assetType": asset_type,
        "lastDate": "2026-07-20",
        "metrics": {
            "return20dPct": -18.0 if stressed else -2.0,
            "realizedVol20dPct": 52.0 if stressed else 24.0,
            "realizedVol60dPct": 25.0 if stressed else 22.0,
            "volPercentile252d": 99.0 if stressed else 62.0,
            "drawdown252dPct": -26.0 if stressed else -5.0,
            "maxAbsDailyMove20dPct": 7.5 if stressed else 2.5,
        },
    }


def test_issuance_stance_covers_four_operating_zones():
    module = load_els_module()

    assert module._issuance_stance(75, 30)["label"] == "발행기회"
    assert module._issuance_stance(75, 55)["label"] == "헤지주의"
    assert module._issuance_stance(90, 85)["label"] == "발행부담"
    assert module._issuance_stance(45, 20)["label"] == "선별발행"


def test_long_history_is_merged_with_fresher_recent_prices(monkeypatch):
    module = load_els_module()
    historical = pd.DataFrame(
        {"date": ["2020-01-02", "2026-07-16"], "close": [100.0, 200.0]}
    )
    recent = pd.DataFrame(
        {"date": ["2026-07-16", "2026-07-21"], "close": [201.0, 210.0]}
    )
    cached = pd.DataFrame(
        {"date": ["2026-07-21", "2026-07-22"], "close": [211.0, 220.0]}
    )

    monkeypatch.setattr(
        module,
        "_fetch_yahoo",
        lambda _symbol, range_value: historical if range_value == "10y" else recent,
    )
    merged = module._fetch_price_history("TEST", cached)

    assert merged["date"].tolist() == ["2020-01-02", "2026-07-16", "2026-07-21", "2026-07-22"]
    assert merged.loc[merged["date"] == "2026-07-16", "close"].iloc[0] == 201.0
    assert merged.loc[merged["date"] == "2026-07-21", "close"].iloc[0] == 210.0


def test_yahoo_daily_rows_exclude_incomplete_exchange_session():
    module = load_els_module()
    session_start = int(datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc).timestamp())
    session_end = int(datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc).timestamp())
    previous_bar = int(datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc).timestamp())
    result = {
        "meta": {
            "exchangeTimezoneName": "Asia/Tokyo",
            "currentTradingPeriod": {"regular": {"start": session_start, "end": session_end}},
        },
        "timestamp": [previous_bar, session_start],
        "indicators": {"quote": [{"close": [65000.0, 66000.0]}]},
    }

    rows, excluded_date = module._yahoo_daily_rows(result, now_timestamp=session_start + 60)

    assert rows == [{"date": "2026-08-07", "close": 65000.0}]
    assert excluded_date == "2026-08-10"


def test_cached_incomplete_session_is_not_reintroduced(monkeypatch):
    module = load_els_module()
    completed = pd.DataFrame({"date": ["2026-08-07"], "close": [65000.0]})
    completed.attrs["excludedIncompleteSessionDate"] = "2026-08-10"
    cached = pd.DataFrame({"date": ["2026-08-10"], "close": [66000.0]})
    monkeypatch.setattr(module, "_fetch_yahoo", lambda _symbol, _range: completed.copy())

    merged = module._fetch_price_history("^N225", cached)

    assert merged["date"].tolist() == ["2026-08-07"]


def test_trajectory_windows_include_one_week_momentum_period():
    module = load_els_module()

    assert module.TRAJECTORY_WINDOWS == {
        "oneWeekPoints": 5,
        "oneMonthPoints": 22,
        "threeMonthPoints": 66,
    }


def test_single_stock_specs_use_korean_common_stock_tickers():
    module = load_els_module()

    assert {(item["id"], item["symbol"], item["assetType"]) for item in module.SINGLE_STOCKS} == {
        ("samsung", "005930.KS", "single-stock"),
        ("hynix", "000660.KS", "single-stock"),
    }


def test_cached_price_history_includes_single_stocks(tmp_path):
    module = load_els_module()
    cache_file = tmp_path / "els-index-risk.json"
    cache_file.write_text(
        """
        {
          "indices": [{"id": "spx", "ytdPriceSeries": [{"date": "2026-01-02", "close": 100}]}],
          "singleStocks": [{"id": "samsung", "ytdPriceSeries": [{"date": "2026-01-02", "close": 90000}]}]
        }
        """,
        encoding="utf-8",
    )

    cached = module._load_cached_price_history(cache_file)

    assert set(cached) == {"spx", "samsung"}
    assert cached["samsung"].iloc[-1]["close"] == 90000


def test_issuance_hedge_item_is_bounded_and_explainable():
    module = load_els_module()
    item = module._issuance_hedge_item(
        {
            "id": "test",
            "label": "TEST",
            "name": "Test Index",
            "region": "테스트",
            "lastDate": "2026-07-20",
            "metrics": {
                "return20dPct": -12.0,
                "realizedVol20dPct": 38.0,
                "realizedVol60dPct": 24.0,
                "volPercentile252d": 90.0,
                "drawdown252dPct": -18.0,
                "maxAbsDailyMove20dPct": 5.0,
            },
        },
        correlation_score=60.0,
    )

    assert 0 <= item["opportunityScore"] <= 100
    assert 0 <= item["hedgeBurdenScore"] <= 100
    assert item["stance"] == "헤지주의"
    assert item["interpretation"]
    assert set(item["components"]) == {
        "volPercentileScore",
        "volLevelScore",
        "volShockScore",
        "downsideMomentumScore",
        "drawdownScore",
        "gapShockScore",
        "correlationScore",
    }


def test_single_stocks_are_mapped_but_do_not_change_index_basket():
    module = load_els_module()
    indices = [sample_asset(spec["id"], spec["label"]) for spec in module.INDICES]
    stocks = [
        sample_asset("samsung", "삼성전자", "single-stock", stressed=True),
        sample_asset("hynix", "SK하이닉스", "single-stock", stressed=True),
    ]

    index_only = module._issuance_hedge_map(indices, correlation_score=55.0)
    with_stocks = module._issuance_hedge_map(
        indices,
        correlation_score=55.0,
        single_stocks=stocks,
    )

    assert with_stocks["basket"] == index_only["basket"]
    assert len(with_stocks["items"]) == 5
    assert {item["id"] for item in with_stocks["singleStocks"]} == {"samsung", "hynix"}
    assert all(item["assetType"] == "single-stock" for item in with_stocks["singleStocks"])
    assert all(not item["includedInBasket"] for item in with_stocks["singleStocks"])
    assert all(not item["includedInStressEpisodes"] for item in with_stocks["singleStocks"])


def test_historical_correlation_score_does_not_use_future_returns():
    module = load_els_module()
    dates = pd.bdate_range("2026-01-02", periods=120).strftime("%Y-%m-%d")
    rng = np.random.default_rng(42)
    frames = [
        pd.DataFrame(
            {
                "date": dates,
                "ret_1d": rng.normal(0, 0.01, len(dates)),
                "index_id": index_id,
            }
        )
        for index_id in ["a", "b", "c"]
    ]
    cutoff = dates[70]
    original = module._correlation_score_series(frames)

    changed_frames = [frame.copy() for frame in frames]
    common_future = np.sin(np.arange(len(dates) - 71)) * 0.03
    for frame in changed_frames:
        frame.loc[frame["date"] > cutoff, "ret_1d"] = common_future
    changed = module._correlation_score_series(changed_frames)

    assert changed[cutoff] == original[cutoff]
    assert abs(changed[dates[-1]] - original[dates[-1]]) > 5


def test_trajectory_ends_at_current_map_position():
    module = load_els_module()
    dates = pd.bdate_range("2026-03-02", periods=72).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "ret_20d": np.linspace(-0.08, 0.04, len(dates)),
            "realized_vol_20d": np.linspace(0.34, 0.22, len(dates)),
            "realized_vol_60d": np.linspace(0.27, 0.20, len(dates)),
            "vol_percentile_252d": np.linspace(88, 62, len(dates)),
            "drawdown_252d": np.linspace(-0.16, -0.05, len(dates)),
            "max_abs_daily_move_20d": np.linspace(0.05, 0.025, len(dates)),
        }
    )
    correlation_scores = {date: 55.0 for date in dates}
    trajectory = module._issuance_trajectory(frame, correlation_scores, fallback_correlation_score=55.0)
    latest_metrics = module._metrics_from_feature_row(frame.iloc[-1])
    current_scores = module._issuance_scores(latest_metrics, correlation_score=55.0)

    assert len(trajectory) == 66
    assert trajectory[-1]["date"] == dates[-1]
    assert trajectory[-1]["opportunityScore"] == current_scores["opportunityScore"]
    assert trajectory[-1]["hedgeBurdenScore"] == current_scores["hedgeBurdenScore"]


def test_stress_episode_trajectory_is_bounded_and_ignores_future_prices():
    module = load_els_module()
    dates = pd.bdate_range("2025-01-02", periods=190).strftime("%Y-%m-%d")
    close = 100 * np.cumprod(1 + 0.002 * np.sin(np.arange(len(dates)) / 4))
    frame = module._features(pd.DataFrame({"date": dates, "close": close}))
    start_date, peak_date, end_date = dates[90], dates[125], dates[160]
    correlation_scores = {date: 55.0 for date in dates}

    original = module._episode_trajectory(
        frame,
        correlation_scores,
        55.0,
        start_date,
        peak_date,
        end_date,
        max_points=14,
    )
    changed_close = close.copy()
    changed_close[161:] *= np.linspace(1.0, 1.8, len(changed_close[161:]))
    changed_frame = module._features(pd.DataFrame({"date": dates, "close": changed_close}))
    changed = module._episode_trajectory(
        changed_frame,
        correlation_scores,
        55.0,
        start_date,
        peak_date,
        end_date,
        max_points=14,
    )

    assert 3 <= len(original) <= 15
    assert original[0]["date"] >= start_date
    assert original[-1]["date"] <= end_date
    assert original == changed
    assert min(original, key=lambda point: abs(pd.Timestamp(point["date"]) - pd.Timestamp(peak_date)))["date"] == peak_date


def test_stress_episode_history_covers_all_five_underlyings():
    module = load_els_module()
    dates = pd.bdate_range("2024-01-02", periods=360).strftime("%Y-%m-%d")
    close = 100 * np.cumprod(1 + 0.003 * np.sin(np.arange(len(dates)) / 5))
    frame = module._features(pd.DataFrame({"date": dates, "close": close}))
    frames = {spec["id"]: frame for spec in [*module.INDICES, *module.SINGLE_STOCKS]}
    correlation_scores = {date: 60.0 for date in dates}
    episode = {
        "id": "sample-stress",
        "label": "샘플 스트레스",
        "startDate": dates[180],
        "peakDate": dates[220],
        "endDate": dates[260],
        "peakScore": 82.0,
    }

    history = module._stress_episode_history([episode], frames, correlation_scores, 60.0)

    assert history["defaultEpisodeId"] == "sample-stress"
    assert len(history["items"]) == 1
    assert len(history["items"][0]["items"]) == 5
    assert {item["id"] for item in history["items"][0]["items"]} == {
        "spx",
        "sx5e",
        "nky",
        "hscei",
        "kospi200",
    }

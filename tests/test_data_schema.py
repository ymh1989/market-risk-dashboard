from __future__ import annotations

import pytest

from kospi_risk.data_schema import available_optional_columns, validate_market_columns


def test_required_columns_are_enforced():
    validate_market_columns({"date", "KOSPI", "SPX", "SOX", "USDKRW"})
    with pytest.raises(ValueError, match="Missing required"):
        validate_market_columns({"date", "KOSPI", "SPX", "USDKRW"})


def test_optional_columns_are_detected_without_being_required():
    assert available_optional_columns({"date", "KOSPI", "SPX", "SOX", "USDKRW", "VIX"}) == ["VIX"]

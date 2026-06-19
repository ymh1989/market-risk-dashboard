from __future__ import annotations

REQUIRED_COLUMNS = ["date", "KOSPI", "SPX", "SOX", "USDKRW"]

OPTIONAL_COLUMNS = [
    "KOSPI200",
    "VKOSPI",
    "VIX",
    "NASDAQ",
    "NIKKEI225",
    "HSCEI",
    "CSI300",
    "US10Y",
    "US2Y",
    "KR10Y",
    "WTI",
    "COPPER",
    "GOLD",
    "FOREIGNER_KOSPI_NET_BUY",
    "FOREIGNER_FUTURES_NET_BUY",
    "INSTITUTION_KOSPI_NET_BUY",
    "KOSPI_PUT_CALL_RATIO",
    "KOSPI_ATM_IV",
    "KOSPI_SKEW",
    "KOSPI_FUTURES_BASIS",
    "CREDIT_SPREAD_KR",
]

PRICE_COLUMNS = [
    "KOSPI",
    "SPX",
    "SOX",
    "USDKRW",
    "KOSPI200",
    "VKOSPI",
    "VIX",
    "NASDAQ",
    "NIKKEI225",
    "HSCEI",
    "CSI300",
    "US10Y",
    "US2Y",
    "KR10Y",
    "WTI",
    "COPPER",
    "GOLD",
    "KOSPI_ATM_IV",
    "KOSPI_SKEW",
    "KOSPI_PUT_CALL_RATIO",
    "KOSPI_FUTURES_BASIS",
    "CREDIT_SPREAD_KR",
]


def validate_market_columns(columns: list[str] | set[str]) -> None:
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        raise ValueError(f"Missing required input columns: {missing}")


def available_optional_columns(columns: list[str] | set[str]) -> list[str]:
    return [column for column in OPTIONAL_COLUMNS if column in columns]

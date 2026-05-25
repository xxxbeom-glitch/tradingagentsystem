"""pykrx 시장 데이터 전용 모듈."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv
from pykrx import stock

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "api", "pykrx_api.log")

EXCLUDED_NAME_KEYWORDS = ("ETF", "ETN", "리츠", "REIT", "스팩", "SPAC", "DR", "인버스", "레버리지")
PREFERRED_SUFFIXES = ("우", "우B", "우C", "우(전환)")


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("pykrx_api")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def _resolve_date(date: str | None = None) -> str:
    if date:
        return date.replace("-", "")
    return datetime.now().strftime("%Y%m%d")


def _find_latest_trading_date(date: str, max_lookback: int = 10) -> str:
    target = datetime.strptime(date, "%Y%m%d")
    for offset in range(max_lookback):
        candidate = (target - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            tickers = stock.get_market_ticker_list(candidate, market="KOSPI")
            if tickers:
                return candidate
        except Exception:
            continue
    return date


def get_volume_rank(
    date: str | None = None,
    market: str = "ALL",
    top_n: int = 30,
) -> list[dict[str, Any]]:
    """거래량 순위 (코스피/코스닥/전체)."""
    trade_date = _find_latest_trading_date(_resolve_date(date))
    markets = ["KOSPI", "KOSDAQ"] if market.upper() == "ALL" else [market.upper()]
    rows: list[dict[str, Any]] = []

    try:
        for mkt in markets:
            df = stock.get_market_ohlcv_by_ticker(trade_date, market=mkt)
            if df is None or df.empty:
                continue
            df = df.sort_values("거래량", ascending=False).head(top_n)
            for ticker, row in df.iterrows():
                rows.append(
                    {
                        "date": trade_date,
                        "market": mkt,
                        "ticker": str(ticker).zfill(6),
                        "name": stock.get_market_ticker_name(ticker),
                        "close": int(row["종가"]),
                        "volume": int(row["거래량"]),
                        "change_rate": float(row.get("등락률", 0) or 0),
                    }
                )
        rows.sort(key=lambda x: x["volume"], reverse=True)
        rows = rows[:top_n]
        logger.info("get_volume_rank | date=%s market=%s count=%d", trade_date, market, len(rows))
        return rows
    except Exception as exc:
        logger.error("get_volume_rank failed: %s", exc)
        raise


def get_supply_flow(
    date: str | None = None,
    market: str = "ALL",
    top_n: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """외국인/기관 수급 상위 종목."""
    end_date = _find_latest_trading_date(_resolve_date(date))
    start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=7)
    start_date = start_dt.strftime("%Y%m%d")
    markets = ["KOSPI", "KOSDAQ"] if market.upper() == "ALL" else [market.upper()]
    result: dict[str, list[dict[str, Any]]] = {"foreign": [], "institution": []}

    try:
        for investor, key in (("외국인", "foreign"), ("기관합계", "institution")):
            combined: list[dict[str, Any]] = []
            for mkt in markets:
                df = stock.get_market_net_purchases_of_equities(
                    start_date, end_date, mkt, investor
                )
                if df is None or df.empty:
                    continue
                net_col = "순매수거래대금" if "순매수거래대금" in df.columns else df.columns[-1]
                ranked = df.sort_values(net_col, ascending=False).head(top_n)
                for ticker, row in ranked.iterrows():
                    combined.append(
                        {
                            "date": end_date,
                            "market": mkt,
                            "ticker": str(ticker).zfill(6),
                            "name": stock.get_market_ticker_name(ticker),
                            "net_amount": int(row[net_col]),
                            "investor": investor,
                        }
                    )
            combined.sort(key=lambda x: x["net_amount"], reverse=True)
            result[key] = combined[:top_n]

        logger.info(
            "get_supply_flow | date=%s foreign=%d institution=%d",
            end_date,
            len(result["foreign"]),
            len(result["institution"]),
        )
        return result
    except Exception as exc:
        logger.error("get_supply_flow failed: %s", exc)
        raise


def get_sector_change(
    date: str | None = None,
    market: str = "KOSDAQ",
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """업종(지수) 등락률 순위."""
    end_date = _find_latest_trading_date(_resolve_date(date))
    start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=5)
    start_date = _find_latest_trading_date(start_dt.strftime("%Y%m%d"))

    try:
        index_tickers = stock.get_index_ticker_list(end_date, market=market)
        rows: list[dict[str, Any]] = []
        for idx in index_tickers:
            try:
                name = stock.get_index_ticker_name(idx)
                ohlcv = stock.get_index_ohlcv_by_date(start_date, end_date, idx)
            except Exception:
                continue
            if ohlcv is None or ohlcv.empty or "종가" not in ohlcv.columns:
                continue
            first_close = float(ohlcv.iloc[0]["종가"])
            last_close = float(ohlcv.iloc[-1]["종가"])
            if first_close == 0:
                continue
            change_rate = round((last_close / first_close - 1) * 100, 2)
            rows.append(
                {
                    "date": end_date,
                    "market": market,
                    "sector_code": idx,
                    "sector_name": name,
                    "change_rate": change_rate,
                    "close": int(last_close),
                }
            )
        rows.sort(key=lambda x: x["change_rate"], reverse=True)
        rows = rows[:top_n]
        logger.info("get_sector_change | date=%s count=%d", end_date, len(rows))
        return rows
    except Exception as exc:
        logger.error("get_sector_change failed: %s", exc)
        raise


def validate_stock(
    ticker: str,
    date: str | None = None,
    max_price: int | None = 59000,
) -> dict[str, Any]:
    """종목 유효성 검사 (보통주 여부, 제외 종목, 가격 조건)."""
    code = str(ticker).zfill(6)
    trade_date = _find_latest_trading_date(_resolve_date(date))
    reasons: list[str] = []

    try:
        name = stock.get_market_ticker_name(code)
        market = None
        for mkt in ("KOSPI", "KOSDAQ"):
            listed = stock.get_market_ticker_list(trade_date, market=mkt)
            if code in [str(t).zfill(6) for t in listed]:
                market = mkt
                break

        if not market:
            reasons.append("코스피/코스닥 상장 종목 아님")

        etf_tickers = set(stock.get_etf_ticker_list(trade_date))
        if code in {str(t).zfill(6) for t in etf_tickers}:
            reasons.append("ETF/ETN 종목")

        upper_name = (name or "").upper()
        for keyword in EXCLUDED_NAME_KEYWORDS:
            if keyword in upper_name:
                reasons.append(f"제외 키워드 포함: {keyword}")

        if any(name.endswith(suffix) for suffix in PREFERRED_SUFFIXES) or "우선주" in name:
            reasons.append("우선주")

        ohlcv = stock.get_market_ohlcv_by_date(
            trade_date, trade_date, code
        )
        close_price = int(ohlcv.iloc[-1]["종가"]) if ohlcv is not None and not ohlcv.empty else None

        if max_price is not None and close_price and close_price > max_price:
            reasons.append(f"신규 진입 가격 초과 ({close_price:,}원 > {max_price:,}원)")

        valid = len(reasons) == 0
        result = {
            "ticker": code,
            "name": name,
            "market": market,
            "date": trade_date,
            "close_price": close_price,
            "valid": valid,
            "reasons": reasons,
        }
        logger.info("validate_stock | %s valid=%s reasons=%s", code, valid, reasons)
        return result
    except Exception as exc:
        logger.error("validate_stock failed | %s: %s", code, exc)
        return {
            "ticker": code,
            "name": None,
            "market": None,
            "date": trade_date,
            "close_price": None,
            "valid": False,
            "reasons": [f"검증 오류: {exc}"],
        }

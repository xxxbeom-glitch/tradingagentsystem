"""시장 감시 모듈.

pykrx로 전체 종목 스캔 → 트리거 조건 체크 → 후보 종목 추출.
신규 진입 조건: 1주당 59,000원 이하 종목만.
트리거 2개 이상 충족 종목만 AI 호출 대상.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from pykrx import stock

from triggers.conditions import (
    check_volume_surge,
    check_institution_buy_turn,
    check_foreign_consecutive_buy,
    check_sector_top3,
    check_52week_high,
    count_triggers,
)

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "market", "market.log")

MAX_ENTRY_PRICE = 59_000  # 신규 진입 가격 상한


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("market")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def get_market_ohlcv(date: str | None = None) -> dict[str, Any]:
    """
    pykrx로 코스피/코스닥 전체 종목 OHLCV 조회.
    date: YYYYMMDD 형식, None이면 전일 기준
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    try:
        df = stock.get_market_ohlcv_by_ticker(date)
        logger.info("시장 데이터 조회 완료 | date=%s | 종목수=%d", date, len(df))
        return df.to_dict(orient="index")
    except Exception as e:
        logger.error("시장 데이터 조회 실패 | date=%s | error=%s", date, e)
        return {}


def get_market_cap(date: str | None = None) -> dict[str, Any]:
    """pykrx로 전체 종목 시가총액 조회."""
    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    try:
        df = stock.get_market_cap_by_ticker(date)
        return df.to_dict(orient="index")
    except Exception as e:
        logger.error("시가총액 조회 실패 | error=%s", e)
        return {}


def get_sector_trend(date: str | None = None) -> list[dict[str, Any]]:
    """업종별 등락률 조회."""
    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    try:
        df = stock.get_index_ohlcv_by_date(date, date, "1001")  # 코스피
        logger.info("업종 등락률 조회 완료")
        return []
    except Exception as e:
        logger.error("업종 등락률 조회 실패 | error=%s", e)
        return []


def scan_candidates(
    ohlcv: dict[str, Any],
    prev_ohlcv: dict[str, Any] | None = None,
    foreign_net_map: dict[str, int] | None = None,
    institution_net_map: dict[str, int] | None = None,
    sector_rank_map: dict[str, int] | None = None,
    high_52week_map: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """
    전체 종목 스캔 → 트리거 2개 이상 충족 + 59,000원 이하 종목 추출.
    
    반환: [
        {
            "ticker": str,
            "name": str,
            "current_price": int,
            "volume": int,
            "change_rate": float,
            "triggers": [충족된 트리거 이름 목록],
            "trigger_count": int,
            "foreign_net": int,
            "institution_net": int,
        },
        ...
    ]
    """
    candidates = []

    for ticker, data in ohlcv.items():
        try:
            current_price = int(data.get("종가", 0))
            volume = int(data.get("거래량", 0))
            change_rate = float(data.get("등락률", 0))

            # 신규 진입 가격 상한 필터
            if current_price <= 0 or current_price > MAX_ENTRY_PRICE:
                continue

            # 거래량 없는 종목 제외
            if volume == 0:
                continue

            trigger_results = []
            trigger_names = []

            # 1. 거래량 급증
            prev_volume = 0
            if prev_ohlcv and ticker in prev_ohlcv:
                prev_volume = int(prev_ohlcv[ticker].get("거래량", 0))
            r = check_volume_surge(ticker, volume, prev_volume)
            trigger_results.append(r)
            if r["triggered"]:
                trigger_names.append("거래량급증")

            # 2. 기관 순매수 전환
            today_inst = institution_net_map.get(ticker, 0) if institution_net_map else 0
            yesterday_inst = 0  # 전일 데이터 없으면 0
            r = check_institution_buy_turn(ticker, today_inst, yesterday_inst)
            trigger_results.append(r)
            if r["triggered"]:
                trigger_names.append("기관순매수전환")

            # 3. 외국인 3일 연속 순매수
            foreign_list = []
            if foreign_net_map and ticker in foreign_net_map:
                foreign_list = [foreign_net_map[ticker]]
            r = check_foreign_consecutive_buy(ticker, foreign_list)
            trigger_results.append(r)
            if r["triggered"]:
                trigger_names.append("외국인연속순매수")

            # 4. 업종 상위 3위
            sector_rank = sector_rank_map.get(ticker, 999) if sector_rank_map else 999
            r = check_sector_top3(sector_rank)
            trigger_results.append(r)
            if r["triggered"]:
                trigger_names.append("업종상위3위")

            # 5. 52주 신고가
            high_52 = high_52week_map.get(ticker, 0) if high_52week_map else 0
            r = check_52week_high(ticker, current_price, high_52)
            trigger_results.append(r)
            if r["triggered"]:
                trigger_names.append("52주신고가")

            trigger_count = count_triggers(trigger_results)

            # 트리거 2개 이상 충족 종목만
            if trigger_count >= 2:
                name = stock.get_market_ticker_name(ticker) or ticker
                candidates.append({
                    "ticker": ticker,
                    "name": name,
                    "current_price": current_price,
                    "volume": volume,
                    "change_rate": change_rate,
                    "triggers": trigger_names,
                    "trigger_count": trigger_count,
                    "foreign_net": foreign_net_map.get(ticker, 0) if foreign_net_map else 0,
                    "institution_net": institution_net_map.get(ticker, 0) if institution_net_map else 0,
                })

        except Exception as e:
            logger.warning("종목 스캔 오류 | ticker=%s | error=%s", ticker, e)
            continue

    # 트리거 개수 많은 순으로 정렬
    candidates.sort(key=lambda x: x["trigger_count"], reverse=True)
    logger.info("후보 종목 스캔 완료 | 후보수=%d", len(candidates))
    return candidates


def get_52week_high(ticker: str) -> int:
    """단일 종목 52주 신고가 조회."""
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df.empty:
            return 0
        return int(df["고가"].max())
    except Exception as e:
        logger.error("52주 신고가 조회 실패 | ticker=%s | error=%s", ticker, e)
        return 0

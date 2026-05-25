"""DART 공시 수집 전용 모듈."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

import dart_fss as dart
from dart_fss.api.filings.search_filings import search_filings
from dart_fss.errors.errors import NoDataReceived
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "trading_agent.db")
LOG_PATH = os.path.join(ROOT_DIR, "logs", "api", "dart_api.log")

dart.enable_spinner(False)

POSITIVE_KEYWORDS = (
    "실적", "흑자", "증가", "호조", "수주", "계약", "상향", "최대", "돌파", "승인",
)
NEGATIVE_KEYWORDS = (
    "손실", "적자", "감소", "하향", "관리", "경고", "위험", "거절", "상장폐지",
    "소송", "횡령", "유상증자", "감사의견", "거절", "불성실", "과열", "매도", "해지",
)
RISK_KEYWORDS = (
    "관리종목", "투자경고", "투자위험", "투자주의", "상장폐지", "감사의견 거절",
    "감사의견한정", "불성실공시", "영업손실", "자본잠식", "소송", "횡령",
    "유상증자", "감자", "상장폐지", "거래정지",
)


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("dart_api")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def _api_key() -> str:
    key = os.getenv("DART_API_KEY", "")
    if not key:
        raise ValueError("DART_API_KEY 환경변수가 설정되지 않았습니다.")
    dart.set_api_key(key)
    return key


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _normalize_filing(item: dict[str, Any]) -> dict[str, Any]:
    title = (item.get("report_nm") or "").strip()
    ticker = (item.get("stock_code") or "").strip() or None
    if ticker:
        ticker = str(ticker).zfill(6)

    summary = title
    sentiment = classify_sentiment(title, summary)

    return {
        "disclosed_at": item.get("rcept_dt") or _today_str(),
        "ticker": ticker,
        "name": (item.get("corp_name") or "").strip(),
        "title": title,
        "summary": summary,
        "sentiment": sentiment,
        "corp_code": item.get("corp_code"),
        "rcept_no": item.get("rcept_no"),
        "source": "DART",
    }


def classify_sentiment(title: str, summary: str = "") -> str:
    """공시 제목/요약 기반 긍정·부정·중립 분류."""
    text = f"{title} {summary}".strip()
    if any(k in text for k in NEGATIVE_KEYWORDS):
        return "부정"
    if any(k in text for k in POSITIVE_KEYWORDS):
        return "긍정"
    return "중립"


def get_today_disclosures(page_count: int = 100) -> list[dict[str, Any]]:
    """오늘 공시 목록."""
    _api_key()
    today = _today_str()
    try:
        result = search_filings(bgn_de=today, end_de=today, page_count=page_count)
        items = [_normalize_filing(row) for row in result.get("list", [])]
        logger.info("get_today_disclosures | date=%s count=%d", today, len(items))
        return items
    except NoDataReceived:
        logger.info("get_today_disclosures | date=%s no data", today)
        return []
    except Exception as exc:
        logger.error("get_today_disclosures failed: %s", exc)
        raise


def get_disclosures_by_ticker(
    ticker: str,
    days: int = 7,
    page_count: int = 50,
) -> list[dict[str, Any]]:
    """종목별 최근 공시."""
    _api_key()
    code = str(ticker).zfill(6)
    corp_list = dart.get_corp_list()
    corp = corp_list.find_by_stock_code(code)
    if corp is None:
        logger.warning("get_disclosures_by_ticker | corp not found: %s", code)
        return []

    from datetime import timedelta

    end_de = _today_str()
    bgn_de = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")

    try:
        filings = corp.search_filings(bgn_de=bgn_de, end_de=end_de)
        items: list[dict[str, Any]] = []
        if filings is None:
            return items

        for report in filings[:page_count]:
            title = getattr(report, "report_nm", "") or ""
            rcept_dt = getattr(report, "rcept_dt", end_de)
            items.append(
                {
                    "disclosed_at": rcept_dt,
                    "ticker": code,
                    "name": corp.corp_name,
                    "title": title.strip(),
                    "summary": title.strip(),
                    "sentiment": classify_sentiment(title),
                    "rcept_no": getattr(report, "rcept_no", None),
                    "source": "DART",
                }
            )
        logger.info("get_disclosures_by_ticker | %s count=%d", code, len(items))
        return items
    except Exception as exc:
        logger.error("get_disclosures_by_ticker failed | %s: %s", code, exc)
        raise


def check_risk_disclosure(
    ticker: str,
    days: int = 3,
) -> dict[str, Any]:
    """최근 N일 리스크 공시 확인."""
    disclosures = get_disclosures_by_ticker(ticker, days=days)
    risks = []
    for item in disclosures:
        text = f"{item['title']} {item.get('summary', '')}"
        matched = [kw for kw in RISK_KEYWORDS if kw in text]
        if matched or item["sentiment"] == "부정":
            risks.append({**item, "risk_keywords": matched})

    return {
        "ticker": str(ticker).zfill(6),
        "days": days,
        "has_risk": len(risks) > 0,
        "risk_count": len(risks),
        "risks": risks,
    }


def save_to_db(disclosures: list[dict[str, Any]]) -> int:
    """공시 데이터를 disclosure_data 테이블에 저장."""
    if not disclosures:
        return 0

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    inserted = 0

    try:
        for item in disclosures:
            cursor.execute(
                """
                INSERT INTO disclosure_data
                    (disclosed_at, ticker, name, title, summary, sentiment, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.get("disclosed_at"),
                    item.get("ticker"),
                    item.get("name"),
                    item.get("title"),
                    item.get("summary"),
                    item.get("sentiment"),
                    item.get("source", "DART"),
                ),
            )
            inserted += 1
        conn.commit()
        logger.info("save_to_db | inserted=%d", inserted)
        return inserted
    except Exception as exc:
        conn.rollback()
        logger.error("save_to_db failed: %s", exc)
        raise
    finally:
        conn.close()


def collect_and_save_today() -> int:
    """오늘 공시 수집 후 DB 저장."""
    items = get_today_disclosures()
    return save_to_db(items)

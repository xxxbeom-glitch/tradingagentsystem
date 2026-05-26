"""Trading Agent System — 메인 실행 모듈.

전체 흐름:
  1. 시장 방향성 판단
  2. 전체 종목 스캔 (data/market.py)
  3. 트리거 조건 체크 (triggers/conditions.py)
  4. 후보 종목 → 팀 A/B 동시 판단 (agents/team_a.py, team_b.py)
  5. 매수 결정 시 포트폴리오 기록 (tracker/portfolio.py)
  6. 보유 종목 목표가/손절가 체크 → 매도 판단
  7. 결과 로그 기록
"""

from __future__ import annotations

import json
import logging
import os
from dotenv import load_dotenv
load_dotenv()
import sys
from datetime import datetime
from typing import Any

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "system", "main.log")


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("main")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


logger = _setup_logger()

TIMELINE_PATH = os.path.join(ROOT_DIR, "timeline.json")


def add_timeline_event(event_type: str, text: str) -> None:
    """timeline.json에 이벤트 추가."""
    try:
        try:
            with open(TIMELINE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"timeline": []}

        data["timeline"].insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": event_type,
            "text": text,
        })

        # 최대 100개 유지
        data["timeline"] = data["timeline"][:100]

        with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("타임라인 이벤트 추가 실패: %s", e)


def get_current_time_slot() -> str:
    """현재 시간대 반환."""
    now = datetime.now()
    hour = now.hour
    minute = now.minute
    total = hour * 60 + minute

    if total < 8 * 60:
        return "장전"
    elif total < 9 * 60:
        return "프리마켓"
    elif total < 9 * 60 + 30:
        return "변동성구간"
    elif total < 11 * 60:
        return "핵심매수구간"
    elif total < 14 * 60:
        return "중간구간"
    elif total < 15 * 60 + 20:
        return "마감구간"
    else:
        return "장마감"


def is_tradeable_time() -> bool:
    """매수 가능한 시간대인지 확인."""
    slot = get_current_time_slot()
    return slot in ["핵심매수구간", "중간구간", "마감구간"]


def judge_market_direction() -> str:
    """
    시장 방향성 판단 (프리마켓 구간 AI 자율 판단 예정).
    현재는 기본값 '중립' 반환.
    추후 Gemini/DeepSeek 호출로 자율 판단 구현 예정.
    """
    logger.info("시장 방향성 판단 — 현재 기본값 '중립' (추후 AI 자율 판단 구현 예정)")
    return "중립"


def check_exit_conditions(
    team: str,
    holding: dict[str, Any],
    current_price: int,
) -> str | None:
    """
    보유 종목 매도 조건 체크.
    목표가 도달, 손절가 도달 시 '매도' 반환.
    """
    target = holding.get("target_price", 0)
    stop = holding.get("stop_loss", 0)
    ticker = holding.get("ticker", "")

    if target and current_price >= target:
        logger.info("목표가 도달 | team=%s ticker=%s current=%d target=%d",
                    team, ticker, current_price, target)
        return "매도"
    if stop and current_price <= stop:
        logger.info("손절가 도달 | team=%s ticker=%s current=%d stop=%d",
                    team, ticker, current_price, stop)
        return "매도"
    return None


def run_cycle() -> None:
    """메인 실행 사이클 1회."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    slot = get_current_time_slot()
    logger.info("=" * 60)
    logger.info("사이클 시작 | %s | 시간대: %s", now, slot)

    # ── 장 외 시간 스킵 ──
    if slot == "장전":
        logger.info("장 시작 전 — 스킵")
        return
    if slot == "장마감":
        logger.info("장 마감 후 — 신규 진입 금지, 보유 종목 관리만")

    # ── 1. 시장 방향성 ──
    market_direction = judge_market_direction()
    logger.info("시장 방향성: %s", market_direction)

    if market_direction == "비우호적":
        logger.info("시장 비우호적 — 신규 진입 보류")
        return

    # ── 2. 전체 종목 스캔 ──
    try:
        from data.market import get_market_ohlcv, scan_candidates
        logger.info("전체 종목 스캔 시작")
        ohlcv = get_market_ohlcv()
        candidates = scan_candidates(ohlcv)
        logger.info("후보 종목 %d개 발굴", len(candidates))
        for candidate in candidates:
            name = candidate["name"]
            ticker = candidate["ticker"]
            add_timeline_event("트리거", f"{name}({ticker}) {' + '.join(candidate['triggers'])}")
    except Exception as e:
        logger.error("종목 스캔 실패: %s", e)
        candidates = []

    # ── 3. 리스크 체크 ──
    try:
        from tracker.portfolio import check_risk, get_portfolio
        risk_a = check_risk("A")
        risk_b = check_risk("B")
        logger.info("리스크 체크 | 팀A: %s | 팀B: %s",
                    risk_a["message"], risk_b["message"])

        if risk_a["status"] == "운용종료":
            logger.warning("팀 A 운용 종료 — 시드 전액 소진")
        if risk_b["status"] == "운용종료":
            logger.warning("팀 B 운용 종료 — 시드 전액 소진")
    except Exception as e:
        logger.error("리스크 체크 실패: %s", e)
        risk_a = {"status": "정상"}
        risk_b = {"status": "정상"}

    # ── 4. 보유 종목 매도 체크 ──
    if slot != "변동성구간":
        try:
            from data.realtime import KISClient
            from tracker.portfolio import get_portfolio, sell

            client = KISClient()

            for team in ["A", "B"]:
                holdings = get_portfolio(team)
                for holding in holdings:
                    ticker = holding["ticker"]
                    try:
                        price_data = client.get_current_price(ticker)
                        current_price = price_data.get("price", 0)
                        action = check_exit_conditions(team, holding, current_price)
                        if action == "매도":
                            qty = holding.get("buy_quantity", 0)
                            result = sell(team, ticker, current_price, qty)
                            logger.info("매도 실행 | team=%s ticker=%s pnl=%d",
                                        team, ticker, result.get("pnl", 0))
                            add_timeline_event("매도", f"팀{team} — {ticker} 매도 손익: {result.get('pnl', 0):,}원")
                    except Exception as e:
                        logger.error("매도 체크 실패 | team=%s ticker=%s error=%s",
                                     team, ticker, e)
        except Exception as e:
            logger.error("보유 종목 매도 체크 실패: %s", e)

    # ── 5. 신규 진입 (매수 가능 시간대만) ──
    if not is_tradeable_time():
        logger.info("현재 시간대(%s) — 신규 진입 불가", slot)
        return

    if not candidates:
        logger.info("후보 종목 없음 — 신규 진입 없음")
        return

    # ── 6. 팀 A/B AI 판단 ──
    try:
        from agents.team_a import run as run_team_a
        from agents.team_b import run as run_team_b
        from tracker.portfolio import buy, get_balance

    except Exception as e:
        logger.error("모듈 로드 실패: %s", e)
        return

    for candidate in candidates[:3]:  # 최대 3종목 검토
        ticker = candidate["ticker"]
        name = candidate["name"]
        logger.info("후보 종목 분석 | %s(%s) | 트리거: %s",
                    name, ticker, candidate["triggers"])

        stock_data = {
            **candidate,
            "market_direction": market_direction,
            "disclosures": [],
            "portfolio": [],
            "available_cash": 0,
        }

        # 팀 A
        if risk_a["status"] not in ["운용종료", "진입중단"]:
            try:
                bal_a = get_balance("A")
                stock_data_a = {**stock_data, "available_cash": bal_a["cash"]}
                result_a = run_team_a(stock_data_a)
                logger.info("팀 A 판단 | ticker=%s action=%s confidence=%s entry=%s",
                            ticker, result_a.get("action"),
                            result_a.get("confidence"),
                            result_a.get("entry_price"))

                if result_a.get("action") == "매수":
                    entry = result_a.get("entry_price") or candidate["current_price"]
                    qty = result_a.get("quantity", 1)
                    buy("A", ticker, name, entry, qty,
                        result_a.get("type", "단타"),
                        result_a.get("target_price", 0),
                        result_a.get("stop_loss", 0),
                        entry)
                    logger.info("팀 A 매수 기록 완료 | ticker=%s qty=%d entry=%d",
                                ticker, qty, entry)
                    add_timeline_event("매수", f"팀A — {name} {entry:,}원 × {qty}주 (entry: {entry:,}원)")
            except Exception as e:
                logger.error("팀 A 실패 | ticker=%s error=%s", ticker, e)

        # 팀 B
        if risk_b["status"] not in ["운용종료", "진입중단"]:
            try:
                bal_b = get_balance("B")
                stock_data_b = {**stock_data, "available_cash": bal_b["cash"]}
                result_b = run_team_b(stock_data_b)
                logger.info("팀 B 판단 | ticker=%s action=%s confidence=%s verification=%s",
                            ticker, result_b.get("action"),
                            result_b.get("confidence"),
                            result_b.get("verification"))

                if result_b.get("action") == "매수":
                    entry = result_b.get("entry_price") or candidate["current_price"]
                    qty = result_b.get("quantity", 1)
                    buy("B", ticker, name, entry, qty,
                        result_b.get("type", "단타"),
                        result_b.get("target_price", 0),
                        result_b.get("stop_loss", 0),
                        entry)
                    logger.info("팀 B 매수 기록 완료 | ticker=%s qty=%d entry=%d",
                                ticker, qty, entry)
                    add_timeline_event("매수", f"팀B — {name} {entry:,}원 × {qty}주 (Gemini+R1 승인)")
            except Exception as e:
                logger.error("팀 B 실패 | ticker=%s error=%s", ticker, e)

    logger.info("사이클 완료 | %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


import time

if __name__ == "__main__":
    import time
    logger.info("Trading Agent System 시작")
    while True:
        try:
            run_cycle()
        except Exception as e:
            logger.error("사이클 오류: %s", e)

        now = datetime.now()

        # 13시 이후 종료
        if now.hour >= 13:
            logger.info("13시 도달 — 시스템 종료")
            break

        # 장 마감 후 종료
        if now.hour >= 16:
            logger.info("장 마감 — 시스템 종료")
            break

        logger.info("다음 사이클까지 5분 대기...")
        time.sleep(300)

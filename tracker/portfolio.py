"""수익률 추적 모듈.

매수/매도 기록, 잔고 관리, 수익률 계산, 리스크 관리 원칙 적용.
수수료: 매수 0.015% / 매도 0.015% + 증권거래세 0.18% (코스닥 기준)
시드: 팀당 500,000원
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT_DIR, "trading_agent.db")
LOG_PATH = os.path.join(ROOT_DIR, "logs", "portfolio", "portfolio.log")

BUY_FEE_RATE = 0.00015   # 매수 수수료 0.015%
SELL_FEE_RATE = 0.00015  # 매도 수수료 0.015%
TAX_RATE = 0.0018        # 증권거래세 0.18%
SEED = 1_000_000           # 시드머니


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("portfolio")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def buy(
    team: str,
    ticker: str,
    name: str,
    price: int,
    quantity: int,
    trade_type: str = "단타",
    target_price: int = 0,
    stop_loss: int = 0,
    entry_price: int | None = None,
) -> dict[str, Any]:
    """매수 기록. 잔고 차감 및 수수료 적용."""
    fee = int(price * quantity * BUY_FEE_RATE)
    buy_amount = price * quantity
    total_cost = buy_amount + fee
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _conn() as conn:
        # 잔고 확인
        row = conn.execute(
            "SELECT cash FROM balance WHERE team = ?", (team,)
        ).fetchone()
        if not row:
            raise RuntimeError(f"팀 {team} 잔고 없음")
        cash = row["cash"]
        if cash < total_cost:
            raise RuntimeError(f"잔고 부족: 필요 {total_cost:,}원 / 보유 {cash:,}원")

        # 포트폴리오 기록
        conn.execute(
            """
            INSERT INTO portfolio
                (team, ticker, name, buy_price, buy_quantity, bought_at,
                 trade_type, buy_amount, buy_fee, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '보유중')
            """,
            (team, ticker, name, price, quantity, now,
             trade_type, buy_amount, fee),
        )

        # AI 판단 기록
        conn.execute(
            """
            INSERT INTO ai_decision_log
                (decided_at, team, model, stage, action, ticker, name, trade_type,
                 reason, confidence, quantity, target_price, stop_loss, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, team, "portfolio", "매수", "매수", ticker, name, trade_type,
                "", "MEDIUM", quantity, target_price, stop_loss, "",
            ),
        )

        # 잔고 차감
        conn.execute(
            "UPDATE balance SET cash = cash - ? WHERE team = ?",
            (total_cost, team),
        )

    logger.info("매수 | team=%s ticker=%s name=%s price=%d qty=%d fee=%d total=%d",
                team, ticker, name, price, quantity, fee, total_cost)
    return {
        "team": team,
        "ticker": ticker,
        "name": name,
        "price": price,
        "quantity": quantity,
        "fee": fee,
        "total_cost": total_cost,
        "bought_at": now,
    }


def sell(
    team: str,
    ticker: str,
    price: int,
    quantity: int,
) -> dict[str, Any]:
    """매도 기록. 손익 계산 및 잔고 반영."""
    fee = int(price * quantity * SELL_FEE_RATE)
    tax = int(price * quantity * TAX_RATE)
    proceeds = price * quantity - fee - tax
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with _conn() as conn:
        # 보유 종목 확인 (최근 매수 기준)
        row = conn.execute(
            """
            SELECT id, buy_price, buy_quantity, buy_fee
            FROM portfolio
            WHERE team = ? AND ticker = ? AND status = '보유중'
            ORDER BY bought_at DESC LIMIT 1
            """,
            (team, ticker),
        ).fetchone()
        if not row:
            raise RuntimeError(f"보유 종목 없음: team={team} ticker={ticker}")

        buy_price = row["buy_price"]
        buy_fee = row["buy_fee"]
        pnl = proceeds - (buy_price * quantity + buy_fee)

        # 포트폴리오 업데이트
        conn.execute(
            """
            UPDATE portfolio
            SET sell_price = ?, sold_at = ?, sell_fee = ?,
                tax = ?, pnl = ?, status = '매도완료'
            WHERE id = ?
            """,
            (price, now, fee, tax, pnl, row["id"]),
        )

        # 잔고 반영
        conn.execute(
            "UPDATE balance SET cash = cash + ? WHERE team = ?",
            (proceeds, team),
        )

    logger.info("매도 | team=%s ticker=%s price=%d qty=%d fee=%d tax=%d pnl=%d",
                team, ticker, price, quantity, fee, tax, pnl)
    return {
        "team": team,
        "ticker": ticker,
        "price": price,
        "quantity": quantity,
        "fee": fee,
        "tax": tax,
        "proceeds": proceeds,
        "pnl": pnl,
        "sold_at": now,
    }


def get_portfolio(team: str) -> list[dict[str, Any]]:
    """현재 보유 종목 조회."""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT ticker, name, buy_price, buy_quantity, trade_type, bought_at
            FROM portfolio
            WHERE team = ? AND status = '보유중'
            ORDER BY bought_at DESC
            """,
            (team,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_balance(team: str) -> dict[str, Any]:
    """잔고 및 누적 수익률 조회."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT cash FROM balance WHERE team = ?", (team,)
        ).fetchone()
        if not row:
            return {"team": team, "cash": 0, "return_pct": 0.0}

        cash = row["cash"]

        # 미실현 손익 (보유 중 종목)
        open_rows = conn.execute(
            "SELECT buy_price, buy_quantity FROM portfolio WHERE team = ? AND status = '보유중'",
            (team,),
        ).fetchall()
        invested = sum(r["buy_price"] * r["buy_quantity"] for r in open_rows)

        # 실현 손익 합계
        pnl_row = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM portfolio WHERE team = ? AND status = '매도완료'",
            (team,),
        ).fetchone()
        realized_pnl = pnl_row["total_pnl"]

        total_value = cash + invested
        return_pct = round((total_value - SEED) / SEED * 100, 2)

    return {
        "team": team,
        "cash": cash,
        "invested": invested,
        "total_value": total_value,
        "realized_pnl": realized_pnl,
        "return_pct": return_pct,
    }


def get_pnl_summary(team: str) -> dict[str, Any]:
    """수익률 요약: 승률, 총 거래수, 수익/손실 횟수."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT pnl, bought_at, sold_at FROM portfolio WHERE team = ? AND status = '매도완료'",
            (team,),
        ).fetchall()

    if not rows:
        return {"team": team, "total_trades": 0, "win_rate": 0.0, "total_pnl": 0}

    total = len(rows)
    wins = sum(1 for r in rows if r["pnl"] > 0)
    total_pnl = sum(r["pnl"] for r in rows)
    win_rate = round(wins / total * 100, 1)

    return {
        "team": team,
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


def check_risk(team: str) -> dict[str, Any]:
    """
    손실 누적 리스크 체크.
    -10%: 신규 진입 규모 50% 축소
    -20%: 신규 진입 전면 중단
    시드 전액 소진: 운용 종료
    """
    balance = get_balance(team)
    return_pct = balance["return_pct"]
    total_value = balance["total_value"]

    if total_value <= 0:
        status = "운용종료"
        message = "시드 전액 소진 — 운용 종료"
    elif return_pct <= -20:
        status = "진입중단"
        message = f"누적 손실 {return_pct}% — 신규 진입 전면 중단"
    elif return_pct <= -10:
        status = "규모축소"
        message = f"누적 손실 {return_pct}% — 신규 진입 규모 50% 축소"
    else:
        status = "정상"
        message = f"수익률 {return_pct}% — 정상 운용"

    logger.info("리스크 체크 | team=%s status=%s return_pct=%.2f", team, status, return_pct)
    return {
        "team": team,
        "status": status,
        "message": message,
        "return_pct": return_pct,
        "total_value": total_value,
    }

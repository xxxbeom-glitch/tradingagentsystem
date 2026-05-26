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

import pandas as pd

# 오늘 하루 매수/매도 시도 기록 · 미체결 대기 주문 (메모리)
trade_log: list[dict[str, Any]] = []
pending_orders: list[dict[str, Any]] = []

TRADE_LOG_CSV_COLUMNS = [
    "일시", "종목코드", "종목명", "팀", "지정가", "시도시현재가", "체결시현재가", "결과", "사유",
]

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


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _append_trade_log_csv_row(record: dict[str, Any]) -> None:
    """trade_log 1건을 logs/trades/YYYY-MM-DD.csv에 즉시 append."""
    today = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(ROOT_DIR, "logs", "trades")
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, f"{today}.csv")
    row = {col: record.get(col) for col in TRADE_LOG_CSV_COLUMNS}
    write_header = not os.path.isfile(path) or os.path.getsize(path) == 0
    df = pd.DataFrame([row])
    df.to_csv(
        path,
        mode="a",
        header=write_header,
        index=False,
        encoding="utf-8-sig",
    )


def _append_buy_trade_log(
    team: str,
    ticker: str,
    name: str,
    entry_price: int,
    price_at_attempt: int,
    result: str,
    reason: str,
    fill_price: int | None = None,
) -> int:
    """매수 시도 trade_log 기록. 추가된 행 인덱스 반환."""
    entry = {
        "일시": _now_str(),
        "종목코드": ticker,
        "종목명": name,
        "팀": team,
        "지정가": entry_price,
        "시도시현재가": price_at_attempt,
        "체결시현재가": fill_price,
        "결과": result,
        "사유": reason,
    }
    trade_log.append(entry)
    _append_trade_log_csv_row(entry)
    return len(trade_log) - 1


def _update_trade_log(log_index: int, **updates: Any) -> None:
    """trade_log 항목 업데이트 (미체결 → 체결/취소)."""
    if 0 <= log_index < len(trade_log):
        trade_log[log_index].update(updates)
        if "취소" in str(updates.get("결과", "")):
            row = {**trade_log[log_index]}
            row["결과"] = "취소"
            row["사유"] = "15:20 미체결 자동취소"
            _append_trade_log_csv_row(row)


def get_today_trade_log() -> list[dict[str, Any]]:
    """오늘 날짜 trade_log만 반환."""
    today = datetime.now().strftime("%Y-%m-%d")
    return [r for r in trade_log if str(r.get("일시", "")).startswith(today)]


def save_trade_log_csv() -> str:
    """trade_log를 logs/trades/YYYY-MM-DD.csv로 저장 (utf-8-sig)."""
    today = datetime.now().strftime("%Y-%m-%d")
    dir_path = os.path.join(ROOT_DIR, "logs", "trades")
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, f"{today}.csv")

    records = get_today_trade_log()
    if records:
        df = pd.DataFrame(records)
        for col in TRADE_LOG_CSV_COLUMNS:
            if col not in df.columns:
                df[col] = None
        extra_cols = [c for c in df.columns if c not in TRADE_LOG_CSV_COLUMNS]
        df = df[TRADE_LOG_CSV_COLUMNS + extra_cols]
    else:
        df = pd.DataFrame(columns=TRADE_LOG_CSV_COLUMNS)

    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("거래 로그 CSV 저장 | %s (%d건)", path, len(records))
    return path


def _fetch_market_price(kis_client: Any, ticker: str, fallback: int) -> int:
    try:
        data = kis_client.get_current_price(ticker)
        return int(data.get("price") or fallback)
    except Exception as e:
        logger.warning("현재가 조회 실패 | ticker=%s error=%s", ticker, e)
        return fallback


def check_pending_orders(kis_client: Any) -> dict[str, Any]:
    """미체결 지정가 주문 재확인. 15:20 이후 자동 취소."""
    now = datetime.now()
    after_1520 = (now.hour, now.minute) >= (15, 20)
    filled = 0
    cancelled = 0

    for order in list(pending_orders):
        team = order["team"]
        ticker = order["ticker"]
        name = order["name"]
        entry_limit = int(order["entry_price"])
        log_index = int(order.get("log_index", -1))

        try:
            if after_1520:
                cancel_reason = "15:20 만료 — 지정가 미체결 자동 취소"
                _update_trade_log(
                    log_index,
                    결과="취소(15:20만료)",
                    사유=cancel_reason,
                    체결시현재가=None,
                )
                pending_orders.remove(order)
                cancelled += 1
                logger.info("미체결 취소 | team=%s ticker=%s", team, ticker)
                continue

            market_price = _fetch_market_price(kis_client, ticker, entry_limit)
            if entry_limit < market_price:
                continue

            buy(
                team=team,
                ticker=ticker,
                name=name,
                price=int(order["price"]),
                quantity=int(order["quantity"]),
                trade_type=order.get("trade_type", "단타"),
                target_price=int(order.get("target_price", 0)),
                stop_loss=int(order.get("stop_loss", 0)),
                entry_price=entry_limit,
                kis_client=kis_client,
                _skip_fill_check=True,
            )
            fill_reason = (
                f"미체결 후 체결 — 지정가 {entry_limit:,}원 >= 현재가 {market_price:,}원"
            )
            _update_trade_log(
                log_index,
                결과="체결",
                체결시현재가=market_price,
                사유=fill_reason,
            )
            pending_orders.remove(order)
            filled += 1
            logger.info("미체결 체결 | team=%s ticker=%s entry=%d market=%d",
                        team, ticker, entry_limit, market_price)
        except Exception as e:
            logger.error("미체결 처리 실패 | team=%s ticker=%s error=%s", team, ticker, e)

    return {
        "filled": filled,
        "cancelled": cancelled,
        "pending": len(pending_orders),
    }


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
    kis_client: Any | None = None,
    _skip_fill_check: bool = False,
) -> dict[str, Any]:
    """매수 기록. 잔고 차감 및 수수료 적용."""
    entry_limit = int(entry_price if entry_price is not None else price)

    if not _skip_fill_check:
        from data.realtime import KISClient

        client = kis_client or KISClient()
        market_price = _fetch_market_price(client, ticker, int(price))

        if entry_limit < market_price:
            reason = (
                f"지정가 {entry_limit:,}원 < 현재가 {market_price:,}원 — 지정가 미달"
            )
            log_index = _append_buy_trade_log(
                team, ticker, name, entry_limit, market_price,
                "미체결", reason, fill_price=None,
            )
            pending_orders.append({
                "team": team,
                "ticker": ticker,
                "name": name,
                "entry_price": entry_limit,
                "price": int(price),
                "quantity": quantity,
                "trade_type": trade_type,
                "target_price": target_price,
                "stop_loss": stop_loss,
                "price_at_attempt": market_price,
                "log_index": log_index,
                "created_at": _now_str(),
            })
            logger.info(
                "매수 미체결 | team=%s ticker=%s entry=%d market=%d",
                team, ticker, entry_limit, market_price,
            )
            return {
                "team": team,
                "ticker": ticker,
                "name": name,
                "status": "미체결",
                "entry_price": entry_limit,
                "market_price": market_price,
                "reason": reason,
                "log_index": log_index,
            }

        fill_reason = (
            f"지정가 {entry_limit:,}원 >= 현재가 {market_price:,}원 — 체결 조건 충족"
        )
        _append_buy_trade_log(
            team, ticker, name, entry_limit, market_price,
            "체결", fill_reason, fill_price=market_price,
        )

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
    if _skip_fill_check:
        for r in reversed(trade_log):
            if r.get("종목코드") == ticker and str(r.get("팀")) == str(team):
                _append_trade_log_csv_row(r)
                break
    return {
        "team": team,
        "ticker": ticker,
        "name": name,
        "price": price,
        "quantity": quantity,
        "fee": fee,
        "total_cost": total_cost,
        "bought_at": now,
        "status": "체결",
    }


def sell(
    team: str,
    ticker: str,
    price: int,
    quantity: int,
    name: str = "",
    sell_reason: str = "목표가도달",
) -> dict[str, Any]:
    """매도 기록. 손익 계산 및 잔고 반영."""
    fee = int(price * quantity * SELL_FEE_RATE)
    tax = int(price * quantity * TAX_RATE)
    proceeds = price * quantity - fee - tax
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    buy_price = 0

    with _conn() as conn:
        # 보유 종목 확인 (최근 매수 기준)
        row = conn.execute(
            """
            SELECT id, name, buy_price, buy_quantity, buy_fee
            FROM portfolio
            WHERE team = ? AND ticker = ? AND status = '보유중'
            ORDER BY bought_at DESC LIMIT 1
            """,
            (team, ticker),
        ).fetchone()
        if not row:
            raise RuntimeError(f"보유 종목 없음: team={team} ticker={ticker}")

        stock_name = name or row["name"] or ticker
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

    return_pct = (
        round((price - buy_price) / buy_price * 100, 2) if buy_price > 0 else 0.0
    )
    sell_entry = {
        "일시": _now_str(),
        "종목코드": ticker,
        "종목명": stock_name,
        "팀": team,
        "지정가": price,
        "시도시현재가": price,
        "체결시현재가": price,
        "결과": "체결",
        "사유": sell_reason,
        "구분": "매도",
        "매도사유": sell_reason,
        "매수가": buy_price,
        "매도가": price,
        "수익률": return_pct,
    }
    trade_log.append(sell_entry)
    _append_trade_log_csv_row(sell_entry)

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

"""DB·portfolio.json·timeline.json 초기화 (DELETE만 사용, DROP 없음)."""

import json
import os
import sqlite3
from datetime import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT_DIR, "trading_agent.db")
PORTFOLIO_PATH = os.path.join(ROOT_DIR, "portfolio.json")
TIMELINE_PATH = os.path.join(ROOT_DIR, "timeline.json")
SEED_MONEY = 1_000_000  # 팀 A/B 공통 시드


def _empty_team() -> dict:
    return {
        "cash": SEED_MONEY,
        "invested": 0,
        "total_value": SEED_MONEY,
        "return_pct": 0.0,
        "realized_pnl": 0,
        "total_trades": 0,
        "win_rate": 0.0,
        "holdings": [],
        "trades": [],
        "ai_log": [],
    }


def reset_database() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_PATH)
    for table in (
        "balance",
        "portfolio",
        "market_data",
        "disclosure_data",
        "trigger_log",
        "ai_decision_log",
    ):
        conn.execute(f"DELETE FROM {table}")

    conn.execute(
        "INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("A", now, SEED_MONEY, SEED_MONEY, 0, SEED_MONEY, 0, 0.0),
    )
    conn.execute(
        "INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("B", now, SEED_MONEY, SEED_MONEY, 0, SEED_MONEY, 0, 0.0),
    )
    conn.commit()
    conn.close()
    print(f"DB 초기화 완료 — 팀 A/B 각 {SEED_MONEY:,}원")


def reset_portfolio_json() -> None:
    data = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "team_a": _empty_team(),
        "team_b": _empty_team(),
    }
    with open(PORTFOLIO_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"portfolio.json 초기화 완료 — {PORTFOLIO_PATH}")


def reset_timeline_json() -> None:
    with open(TIMELINE_PATH, "w", encoding="utf-8") as f:
        json.dump({"timeline": []}, f, ensure_ascii=False, indent=2)
    print(f"timeline.json 초기화 완료 — {TIMELINE_PATH}")


if __name__ == "__main__":
    reset_database()
    reset_portfolio_json()
    reset_timeline_json()
    print("전체 초기화 완료")

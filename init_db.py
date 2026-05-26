import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "trading_agent.db")

def init_db():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DB 초기화 시작")
    print(f"경로: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ─────────────────────────────────────────
    # 1. 시장 데이터
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS market_data (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            collected_at    TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            name            TEXT,
            current_price   INTEGER,
            volume          INTEGER,
            volume_ratio    REAL,
            foreign_net     INTEGER,
            institution_net INTEGER,
            execution_strength REAL,
            sector          TEXT,
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ market_data 테이블 생성")

    # ─────────────────────────────────────────
    # 2. 공시 데이터
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS disclosure_data (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            disclosed_at    TEXT    NOT NULL,
            ticker          TEXT,
            name            TEXT,
            title           TEXT    NOT NULL,
            summary         TEXT,
            sentiment       TEXT    CHECK(sentiment IN ('긍정', '부정', '중립')),
            source          TEXT    DEFAULT 'DART',
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ disclosure_data 테이블 생성")

    # ─────────────────────────────────────────
    # 3. 트리거 발동 기록
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trigger_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered_at    TEXT    NOT NULL,
            ticker          TEXT    NOT NULL,
            name            TEXT,
            conditions_met  TEXT    NOT NULL,
            conditions_count INTEGER,
            team            TEXT    CHECK(team IN ('A', 'B', 'ALL')),
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ trigger_log 테이블 생성")

    # ─────────────────────────────────────────
    # 4. AI 판단 기록
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ai_decision_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            decided_at      TEXT    NOT NULL,
            team            TEXT    NOT NULL CHECK(team IN ('A', 'B')),
            model           TEXT    NOT NULL,
            stage           TEXT    NOT NULL,
            action          TEXT    NOT NULL CHECK(action IN ('매수', '매도', '관망')),
            ticker          TEXT,
            name            TEXT,
            trade_type      TEXT    CHECK(trade_type IN ('단타', '스윙')),
            reason          TEXT,
            confidence      TEXT    CHECK(confidence IN ('HIGH', 'MEDIUM', 'LOW')),
            quantity        INTEGER,
            target_price    INTEGER,
            stop_loss       INTEGER,
            raw_response    TEXT,
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ ai_decision_log 테이블 생성")

    # ─────────────────────────────────────────
    # 5. 포트폴리오 (거래 내역)
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            team            TEXT    NOT NULL CHECK(team IN ('A', 'B')),
            ticker          TEXT    NOT NULL,
            name            TEXT,
            trade_type      TEXT    CHECK(trade_type IN ('단타', '스윙')),
            buy_price       INTEGER,
            buy_quantity    INTEGER,
            buy_amount      INTEGER,
            buy_fee         REAL,
            bought_at       TEXT,
            sell_price      INTEGER,
            sell_quantity   INTEGER,
            sell_amount     INTEGER,
            sell_fee        REAL,
            tax             REAL,
            sold_at         TEXT,
            pnl             INTEGER,
            pnl_rate        REAL,
            status          TEXT    DEFAULT '보유중' CHECK(status IN ('보유중', '매도완료')),
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ portfolio 테이블 생성")

    # ─────────────────────────────────────────
    # 6. 잔고 현황
    # ─────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS balance (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            team            TEXT    NOT NULL CHECK(team IN ('A', 'B')),
            recorded_at     TEXT    NOT NULL,
            seed_amount     INTEGER DEFAULT 500000,
            cash            INTEGER,
            stock_value     INTEGER,
            total_value     INTEGER,
            pnl             INTEGER,
            pnl_rate        REAL,
            created_at      TEXT    DEFAULT (datetime('now', 'localtime'))
        )
    """)
    print("  ✓ balance 테이블 생성")

    # ─────────────────────────────────────────
    # 초기 잔고 데이터 삽입 (팀 A, B 시드 500,000원)
    # ─────────────────────────────────────────
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("SELECT COUNT(*) FROM balance WHERE team = 'A'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate)
            VALUES ('A', ?, 1000000, 1000000, 0, 1000000, 0, 0.0)
        """, (now,))
        print("  ✓ 팀 A 초기 잔고 1,000,000원 설정")

    cursor.execute("SELECT COUNT(*) FROM balance WHERE team = 'B'")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate)
            VALUES ('B', ?, 1000000, 1000000, 0, 1000000, 0, 0.0)
        """, (now,))
        print("  ✓ 팀 B 초기 잔고 1,000,000원 설정")

    conn.commit()
    conn.close()

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] DB 초기화 완료")
    print(f"파일: {DB_PATH}")

if __name__ == "__main__":
    init_db()

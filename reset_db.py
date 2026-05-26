import sqlite3
from datetime import datetime

DB_PATH = "trading_agent.db"
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

conn = sqlite3.connect(DB_PATH)
conn.execute("DELETE FROM balance")
conn.execute("DELETE FROM portfolio")
conn.execute("DELETE FROM market_data")
conn.execute("DELETE FROM disclosure_data")
conn.execute("DELETE FROM trigger_log")
conn.execute("DELETE FROM ai_decision_log")

conn.execute(
    "INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    ("A", now, 1000000, 1000000, 0, 1000000, 0, 0.0)
)
conn.execute(
    "INSERT INTO balance (team, recorded_at, seed_amount, cash, stock_value, total_value, pnl, pnl_rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    ("B", now, 1000000, 1000000, 0, 1000000, 0, 0.0)
)
conn.commit()
conn.close()
print("DB 초기화 완료 — 팀 A/B 각 1,000,000원")

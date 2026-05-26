"""
1년치 국내 주식 데이터 수집 스크립트
FinanceDataReader (가격/거래량) + pykrx (외국인/기관 수급)

실행 방법:
  python collect_market_data.py

소요 시간: 약 2~3시간 (전체 종목 기준)
"""

import pandas as pd
import FinanceDataReader as fdr
from pykrx import stock as krx
from datetime import datetime, timedelta
from dotenv import load_dotenv
import time
import os

load_dotenv()

# ── 기간 설정 ──────────────────────────────────────────────
END_DATE   = (datetime.today() - timedelta(days=1)).strftime("%Y-%m-%d")
START_DATE = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")
END_KRX    = (datetime.today() - timedelta(days=1)).strftime("%Y%m%d")
START_KRX  = (datetime.today() - timedelta(days=365)).strftime("%Y%m%d")

OUTPUT_FILE = "market_data_1year.csv"

print(f"수집 기간: {START_DATE} ~ {END_DATE}")
print("=" * 50)


# ── 1. 전체 종목 리스트 ────────────────────────────────────
print("[1/4] 전체 종목 코드 수집 중...")

df_kospi  = fdr.StockListing("KOSPI")
df_kosdaq = fdr.StockListing("KOSDAQ")
df_all    = pd.concat([df_kospi, df_kosdaq], ignore_index=True)

df_all.columns = [c.strip() for c in df_all.columns]
code_col = "Code" if "Code" in df_all.columns else df_all.columns[0]
name_col = "Name" if "Name" in df_all.columns else df_all.columns[1]

df_all = df_all[[code_col, name_col]].copy()
df_all.columns = ["ticker", "name"]
df_all["ticker"] = df_all["ticker"].astype(str).str.zfill(6)

kospi_codes = set(df_kospi[code_col].astype(str).str.zfill(6).tolist())
df_all["market"] = df_all["ticker"].apply(lambda x: "KOSPI" if x in kospi_codes else "KOSDAQ")

# 우선주 제외
exclude_kw = ["우B", "1우", "2우", "3우", "우C", "우D"]
df_all = df_all[~df_all["name"].apply(lambda x: any(k in str(x) for k in exclude_kw))]
df_all = df_all[~df_all["name"].str.endswith("우")]
df_all = df_all.drop_duplicates(subset="ticker").reset_index(drop=True)

total = len(df_all)
print(f"  → 총 {total}개 종목 (우선주 제외)")

if total == 0:
    print("❌ 종목 리스트가 비어있습니다. 종료합니다.")
    exit()


# ── 2. 종목별 데이터 수집 ──────────────────────────────────
print("[2/4] 종목별 데이터 수집 중... (시간이 걸립니다)")

all_rows = []
errors   = []

for i, row in df_all.iterrows():
    ticker = row["ticker"]
    name   = row["name"]
    market = row["market"]

    try:
        # FinanceDataReader: 가격 + 거래량
        df = fdr.DataReader(ticker, START_DATE, END_DATE)
        if df is None or df.empty:
            continue

        df.index.name = "date"
        df = df.reset_index()

        col_map = {
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
            "Change": "change_rate"
        }
        df.rename(columns=col_map, inplace=True)

        # 거래대금 추정 (종가 × 거래량)
        df["trading_value_억"] = (df["close"] * df["volume"] / 1e8).round(1)

        df["ticker"] = ticker
        df["name"]   = name
        df["market"] = market

        # pykrx: 외국인/기관 순매수 (주식 수 기준)
        try:
            df_inv = krx.get_market_trading_volume_by_date(START_KRX, END_KRX, ticker)
            if not df_inv.empty:
                df_inv.index = pd.to_datetime(df_inv.index)
                df_inv = df_inv.reset_index()
                df_inv.columns = ["date"] + list(df_inv.columns[1:])

                foreign_col = "외국인합계" if "외국인합계" in df_inv.columns else \
                              "외국인" if "외국인" in df_inv.columns else None
                inst_col    = "기관합계" if "기관합계" in df_inv.columns else \
                              "기관" if "기관" in df_inv.columns else None

                merge_cols = ["date"]
                if foreign_col:
                    df_inv = df_inv.rename(columns={foreign_col: "foreign_net"})
                    merge_cols.append("foreign_net")
                if inst_col:
                    df_inv = df_inv.rename(columns={inst_col: "institution_net"})
                    merge_cols.append("institution_net")

                df = pd.merge(df, df_inv[merge_cols], on="date", how="left")

        except Exception:
            df["foreign_net"]     = 0
            df["institution_net"] = 0

        # 단위 변환: 주식 수 → 억원 (현재가 기준 근사)
        if "foreign_net" in df.columns:
            df["foreign_net_억"] = (df["foreign_net"] * df["close"] / 1e8).round(1)
        else:
            df["foreign_net_억"] = 0

        if "institution_net" in df.columns:
            df["institution_net_억"] = (df["institution_net"] * df["close"] / 1e8).round(1)
        else:
            df["institution_net_억"] = 0

        # 시가총액 (pykrx)
        try:
            df_cap = krx.get_market_cap(START_KRX, END_KRX, ticker)
            if not df_cap.empty and "시가총액" in df_cap.columns:
                df_cap.index = pd.to_datetime(df_cap.index)
                df_cap = df_cap.reset_index()
                df_cap.columns = ["date"] + list(df_cap.columns[1:])
                df_cap = df_cap[["date", "시가총액"]].rename(columns={"시가총액": "market_cap_억"})
                df_cap["market_cap_억"] = (df_cap["market_cap_억"] / 1e8).round(0)
                df = pd.merge(df, df_cap, on="date", how="left")
        except Exception:
            df["market_cap_억"] = 0

        all_rows.append(df)

    except Exception as e:
        errors.append(f"{ticker} ({name}): {e}")

    if (i + 1) % 50 == 0:
        elapsed = i + 1
        remaining = total - elapsed
        print(f"  진행: {elapsed}/{total} ({elapsed/total*100:.1f}%) | 오류: {len(errors)}개 | 남은 종목: {remaining}개")

    time.sleep(0.1)  # pykrx 과부하 방지


# ── 3. 데이터 정리 ─────────────────────────────────────────
print("[3/4] 데이터 정리 중...")

if not all_rows:
    print("❌ 수집된 데이터가 없습니다.")
    exit()

df_final = pd.concat(all_rows, ignore_index=True)

for col in ["foreign_net_억", "institution_net_억", "market_cap_억", "change_rate"]:
    if col not in df_final.columns:
        df_final[col] = 0

df_final["date"] = pd.to_datetime(df_final["date"])
df_final = df_final[df_final["close"] > 0].dropna(subset=["close"])
df_final = df_final.sort_values(["ticker", "date"])


# ── 4. 단기 수익률 계산 ────────────────────────────────────
print("[4/4] 단기 수익률 계산 중...")

df_final["return_1d"]  = df_final.groupby("ticker")["close"].pct_change(1).shift(-1)  * 100
df_final["return_3d"]  = df_final.groupby("ticker")["close"].pct_change(3).shift(-3)  * 100
df_final["return_5d"]  = df_final.groupby("ticker")["close"].pct_change(5).shift(-5)  * 100
df_final["return_10d"] = df_final.groupby("ticker")["close"].pct_change(10).shift(-10) * 100

# 최종 컬럼
out_cols = [
    "date", "ticker", "name", "market",
    "close", "change_rate",
    "volume", "trading_value_억",
    "foreign_net_억", "institution_net_억",
    "market_cap_억",
    "return_1d", "return_3d", "return_5d", "return_10d"
]
df_final = df_final[[c for c in out_cols if c in df_final.columns]]
df_final.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

print()
print("=" * 50)
print(f"✅ 완료! 저장 경로: {os.path.abspath(OUTPUT_FILE)}")
print(f"   총 행 수: {len(df_final):,}개")
print(f"   종목 수: {df_final['ticker'].nunique():,}개")
print(f"   기간: {df_final['date'].min().date()} ~ {df_final['date'].max().date()}")

if errors:
    print(f"   오류 종목: {len(errors)}개")
    with open("collect_errors.log", "w", encoding="utf-8") as f:
        f.write("\n".join(errors))
    print(f"   오류 목록: collect_errors.log 참고")

print()
print("다음 단계: market_data_1year.csv 를 Gemini에게 업로드 후 분석 요청")

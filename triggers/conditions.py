"""트리거 조건 판별 모듈.

1. check_volume_surge — 전일 동시간대 대비 거래량 300% 이상 급증
2. check_institution_buy_turn — 기관 순매도 → 순매수 전환
3. check_foreign_consecutive_buy — 외국인 3일 연속 순매수
4. check_sector_top3 — 업종 등락률 상위 3위 이내
5. check_52week_high — 52주 신고가 98% 이상 또는 돌파

count_triggers — 충족 조건 수 집계 (2개 이상 시 AI 호출 대상)
"""

from __future__ import annotations


def check_volume_surge(
    ticker: str,
    current_volume: int,
    prev_volume: int,
) -> dict:
    """현재 거래량이 전일 동시간대 대비 300% 이상이면 triggered."""
    if prev_volume > 0:
        ratio = current_volume / prev_volume
    elif current_volume > 0:
        ratio = float("inf")
    else:
        ratio = 0.0
    triggered = ratio >= 3.0
    return {
        "triggered": triggered,
        "ratio": ratio,
        "current": int(current_volume),
        "prev": int(prev_volume),
    }


def check_institution_buy_turn(
    ticker: str,
    today_net: int,
    yesterday_net: int,
) -> dict:
    """어제 순매도(음수) → 오늘 순매수(양수) 전환 시 triggered."""
    triggered = yesterday_net < 0 and today_net > 0
    return {
        "triggered": triggered,
        "today": int(today_net),
        "yesterday": int(yesterday_net),
    }


def check_foreign_consecutive_buy(
    ticker: str,
    net_buy_list: list[int],
) -> dict:
    """최근 3일(최신순) 외국인 순매수가 모두 양수이면 triggered."""
    days = [int(v) for v in net_buy_list[:3]]
    triggered = len(days) >= 3 and all(d > 0 for d in days)
    return {
        "triggered": triggered,
        "days": days,
    }


def check_sector_top3(sector_rank: int) -> dict:
    """업종 등락률 순위가 상위 3위 이내이면 triggered."""
    rank = int(sector_rank)
    triggered = 1 <= rank <= 3
    return {
        "triggered": triggered,
        "rank": rank,
    }


def check_52week_high(
    ticker: str,
    current_price: int,
    high_52week: int,
) -> dict:
    """현재가가 52주 신고가의 98% 이상이거나 돌파하면 triggered."""
    current = int(current_price)
    high = int(high_52week)
    ratio = current / high if high > 0 else 0.0
    triggered = high > 0 and current >= high * 0.98
    return {
        "triggered": triggered,
        "current": current,
        "high_52week": high,
        "ratio": ratio,
    }


def count_triggers(results: list[dict]) -> int:
    """충족된 트리거 개수 반환. 2개 이상이면 AI 호출 대상."""
    return sum(1 for r in results if r.get("triggered"))

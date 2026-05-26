"""팀 A (스피드팀) 에이전트 모듈.

처리 구조:
  1. Gemini 2.5 Flash — 종목 데이터 분석 + 항목별 점수 JSON 출력
  2. DeepSeek R1 — Gemini 분석 결과 기반 최종 매수/매도/관망 결정

검증 단계 없음. 트리거 조건 충족 시 즉시 결정.
PHILOSOPHY.md의 [TEAM_A_START] ~ [TEAM_A_END] 구간을 프롬프트에 주입.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import google.genai as genai
from openai import OpenAI

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "agents", "team_a.log")
PHILOSOPHY_PATH = os.path.join(ROOT_DIR, "PHILOSOPHY.md")


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("team_a")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def _load_philosophy() -> str:
    """PHILOSOPHY.md에서 [TEAM_A_START] ~ [TEAM_A_END] 구간만 읽어서 반환."""
    try:
        with open(PHILOSOPHY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        start = content.find("[TEAM_A_START]")
        end = content.find("[TEAM_A_END]")
        if start != -1 and end != -1:
            return content[start + len("[TEAM_A_START]"):end].strip()
        # 마커 없으면 전체 반환
        return content.strip()
    except Exception as e:
        logger.warning("PHILOSOPHY.md 로드 실패: %s", e)
        return ""


SYSTEM_PROMPT_TEMPLATE = """너는 한국 주식 단타/스윙 전문 투자 에이전트 — 팀 A (스피드팀)다.
현재 시각은 {datetime} 이다. 이 시각 이후의 정보는 존재하지 않는다.

아래는 이 시스템의 투자 철학 및 운용 원칙이다. 반드시 준수하라.
---
{philosophy}
---

[출력 형식 — 반드시 JSON만 출력, 다른 텍스트 절대 금지]
{{
  "action": "매수/매도/관망",
  "ticker": "종목코드",
  "name": "종목명",
  "type": "단타/스윙",
  "reason": "비전공자도 이해할 수 있는 한 줄 근거",
  "confidence": "HIGH/MEDIUM/LOW",
  "quantity": 수량 (아래 규칙 반드시 준수),
  "entry_price": 희망 진입가 (현재가보다 낮게 설정, 관망 시 null),
  "target_price": 목표가,
  "stop_loss": 손절가,
  "scores": {{
    "수급": 0~10,
    "모멘텀": 0~10,
    "섹터": 0~10,
    "뉴스": 0~10
  }}
}}

[수량 계산 규칙 — 반드시 준수]
- 가용 현금(available_cash) 범위 내에서만 매수
- confidence=HIGH: 가용 현금의 40~50% 사용
- confidence=MEDIUM: 가용 현금의 20~30% 사용
- confidence=LOW: 가용 현금의 10~15% 사용
- quantity = int(가용현금 * 비율 / entry_price)
- quantity가 0이면 action을 관망으로 변경
- 분할 매수: 한 종목에 가용 현금의 50% 초과 금지

[현금/종목 수 제한 — 반드시 준수]
- 최소 현금 보유: 가용 현금(available_cash)이 300,000원 이하이면 action을 반드시 "관망"으로 변경
- 최대 보유 종목: 현재 보유 종목이 3개 이상이면 action을 반드시 "관망"으로 변경
- 위 두 조건 중 하나라도 해당하면 신규 매수 절대 금지

[목표가/손절가 계산 규칙 — 반드시 준수]
- type이 "단타"인 경우:
  target_price = int(entry_price * 1.04)  # +4%
  stop_loss = int(entry_price * 0.98)     # -2%
- type이 "스윙"인 경우:
  target_price = int(entry_price * 1.12)  # +12%
  stop_loss = int(entry_price * 0.95)     # -5%
- 반드시 숫자로 설정할 것 (0이나 null 금지)

[지정가 진입 규칙]
- entry_price는 현재가보다 1~3% 낮게 설정
- 당일 미체결 시 자동 취소 (다음날 연장 금지)
- 매수 확신도가 HIGH일 때만 현재가 근접 진입 허용
"""

USER_PROMPT_TEMPLATE = """현재 시장 데이터:
- 시장 방향성: {market_direction}
- 종목코드: {ticker}
- 종목명: {name}
- 현재가: {current_price}원
- 거래량: {volume}
- 등락률: {change_rate}%
- 외국인 순매수: {foreign_net}
- 기관 순매수: {institution_net}
- 충족된 트리거 조건: {triggers}
- 최근 공시: {disclosures}
- 보유 현황: {portfolio}
- 가용 현금: {available_cash}원

위 데이터 기준으로 판단하라. 반드시 JSON만 출력하라.
"""


def analyze_with_gemini(stock_data: dict[str, Any]) -> str:
    """Gemini Flash로 종목 분석. 분석 결과 텍스트 반환."""
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        philosophy = _load_philosophy()
        system = SYSTEM_PROMPT_TEMPLATE.format(datetime=now, philosophy=philosophy)
        user = USER_PROMPT_TEMPLATE.format(
            market_direction=stock_data.get("market_direction", "중립"),
            ticker=stock_data.get("ticker", ""),
            name=stock_data.get("name", ""),
            current_price=stock_data.get("current_price", 0),
            volume=stock_data.get("volume", 0),
            change_rate=stock_data.get("change_rate", 0),
            foreign_net=stock_data.get("foreign_net", 0),
            institution_net=stock_data.get("institution_net", 0),
            triggers=stock_data.get("triggers", []),
            disclosures=stock_data.get("disclosures", []),
            portfolio=stock_data.get("portfolio", []),
            available_cash=stock_data.get("available_cash", 1000000),
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"{system}\n\n{user}",
        )
        result = (response.text or "").strip()
        logger.info("Gemini 분석 완료 | ticker=%s | result=%s", stock_data.get("ticker"), result[:200])
        return result

    except Exception as e:
        logger.error("Gemini 분석 실패 | ticker=%s | error=%s", stock_data.get("ticker"), e)
        raise


def decide_with_deepseek(gemini_result: str, stock_data: dict[str, Any]) -> dict[str, Any]:
    """DeepSeek R1으로 최종 매수/매도/관망 결정."""
    try:
        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        philosophy = _load_philosophy()
        system = SYSTEM_PROMPT_TEMPLATE.format(datetime=now, philosophy=philosophy)
        user = f"""Gemini Flash의 분석 결과다:
{gemini_result}

위 분석을 검토하고 최종 판단을 내려라.
팀 A 투자 철학과 우선순위에 따라 판단하며 반드시 JSON만 출력하라."""

        response = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1000,
        )

        raw = response.choices[0].message.content.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        logger.info("DeepSeek 결정 완료 | ticker=%s | action=%s | confidence=%s",
                    stock_data.get("ticker"), result.get("action"), result.get("confidence"))
        return result

    except Exception as e:
        logger.error("DeepSeek 결정 실패 | ticker=%s | error=%s", stock_data.get("ticker"), e)
        raise


def run(stock_data: dict[str, Any]) -> dict[str, Any]:
    """팀 A 파이프라인 실행. stock_data를 받아 최종 결정 dict 반환."""
    ticker = stock_data.get("ticker", "")
    logger.info("팀 A 파이프라인 시작 | ticker=%s", ticker)

    try:
        gemini_result = analyze_with_gemini(stock_data)
        final = decide_with_deepseek(gemini_result, stock_data)
        final["team"] = "A"
        final["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("팀 A 파이프라인 완료 | ticker=%s | action=%s", ticker, final.get("action"))
        return final

    except Exception as e:
        logger.error("팀 A 파이프라인 실패 | ticker=%s | error=%s", ticker, e)
        return {
            "team": "A",
            "action": "관망",
            "ticker": ticker,
            "reason": f"파이프라인 오류: {e}",
            "confidence": "LOW",
            "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

"""팀 B (검증팀) 에이전트 모듈.

처리 구조:
  1. DeepSeek V3 — 종목 데이터 수집/분석 + 항목별 점수 JSON 출력
  2. Gemini Pro + DeepSeek R1 병렬 실행
     - Gemini Pro: 종목 최종 결정
     - DeepSeek R1: 반론 검증
  3. 최종 승인/거절 판단
     - Gemini Pro 매수 + R1 승인 → 매수 집행
     - Gemini Pro 매수 + R1 거절 → 관망 (보수적 원칙)

할루시네이션 방지 및 편향 제거를 위한 교차 검증 구조.
PHILOSOPHY.md의 [TEAM_B_START] ~ [TEAM_B_END] 구간을 프롬프트에 주입.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import google.genai as genai
from openai import OpenAI

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "agents", "team_b.log")
PHILOSOPHY_PATH = os.path.join(ROOT_DIR, "PHILOSOPHY.md")


def _setup_logger() -> logging.Logger:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logger = logging.getLogger("team_b")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


logger = _setup_logger()


def _load_philosophy() -> str:
    """PHILOSOPHY.md에서 [TEAM_B_START] ~ [TEAM_B_END] 구간만 읽어서 반환."""
    try:
        with open(PHILOSOPHY_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        start = content.find("[TEAM_B_START]")
        end = content.find("[TEAM_B_END]")
        if start != -1 and end != -1:
            return content[start + len("[TEAM_B_START]"):end].strip()
        return content.strip()
    except Exception as e:
        logger.warning("PHILOSOPHY.md 로드 실패: %s", e)
        return ""


SYSTEM_PROMPT_TEMPLATE = """너는 한국 주식 단타/스윙 전문 투자 에이전트 — 팀 B (검증팀)다.
현재 시각은 {datetime} 이다. 이 시각 이후의 정보는 존재하지 않는다.

아래는 이 시스템의 투자 철학 및 운용 원칙이다. 반드시 준수하라.
---
{philosophy}
---

[팀 B 투자 철학]
"한 번 더 의심하고 들어간다"
- 리스크 관리 중심
- 근거 없이는 들어가지 않는다
- 잃지 않는 것이 먼저

[팀 B 판단 우선순위]
1순위: 리스크 제거 (매수 제외 기준 전체 체크 — 하나라도 해당 시 즉시 탈락)
2순위: 수급 (외국인/기관 순매수 여부)
3순위: 재무 이상 없는지 확인
4순위: 모멘텀 (거래량, 가격)
5순위: 뉴스
→ 1순위 통과 후 2+3+4 충족 시 검토

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
  "exit_reason": "익절가/목표가 설정 근거 한 줄",
  "trailing_stop": true/false,
  "trailing_trigger": 트레일링 스탑 발동 기준 수익률 (예: 5.0),
  "partial_exit": true/false,
  "partial_exit_ratio": 부분 익절 비율 (예: 0.5 = 50%),
  "partial_exit_trigger": 부분 익절 발동 기준 수익률 (예: 5.0),
  "scores": {{
    "리스크": 0~10,
    "수급": 0~10,
    "재무": 0~10,
    "모멘텀": 0~10,
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
- 최대 보유 종목: 현재 보유 종목이 4개 이상이면 action을 반드시 "관망"으로 변경
- 위 두 조건 중 하나라도 해당하면 신규 매수 절대 금지

[목표가/손절가 계산 규칙 — 반드시 준수]
- type이 "단타"인 경우:
  target_price = int(entry_price * 1.04)  # +4%
  stop_loss = int(entry_price * 0.98)     # -2%
- type이 "스윙"인 경우:
  target_price = int(entry_price * 1.12)  # +12%
  stop_loss = int(entry_price * 0.95)     # -5%
- 반드시 숫자로 설정할 것 (0이나 null 금지)
- 단순 범위가 아니라 수급강도/거래량/모멘텀·리스크를 반영해 target_price·stop_loss를 조정할 수 있음
- exit_reason에 위 조정 근거를 반드시 한 줄로 명시

[팀 B 매도·트레일링·부분익절 규칙]
- action이 "매수"일 때 partial_exit=true 권장 (검증 통과·확신 MEDIUM 이상)
- partial_exit_ratio 기본 0.5 (+5% 도달 시 50% 부분 익절)
- partial_exit_trigger 기본 5.0
- trailing_stop=true 시 trailing_trigger 기본 5.0 (+5% 달성 시 손절가를 +2%로 상향)
- 거래량 동반 음봉 시 추세 전환으로 판단 후 매도 검토
- action이 "관망"이면 exit_reason="", trailing_stop=false, trailing_trigger=0, partial_exit=false, partial_exit_ratio=0, partial_exit_trigger=0

[지정가 진입 규칙]
- entry_price는 현재가보다 1~3% 낮게 설정
- 당일 미체결 시 자동 취소 (다음날 연장 금지)
- 매수 확신도가 HIGH일 때만 현재가 근접 진입 허용
"""

VERIFICATION_PROMPT = """너는 한국 주식 투자 리스크 검증 에이전트다.
현재 시각은 {datetime} 이다.

아래는 Gemini Pro의 종목 결정 결과다:
{gemini_decision}

위 결정에 대해 반론 검증을 수행하라.
다음 항목을 반드시 체크하라:
1. 매수 제외 기준(관리종목, 투자경고, 공매도 급증 등)에 해당하지 않는가?
2. 할루시네이션 가능성 — 근거 데이터가 실제로 존재하는가?
3. 리스크 대비 수익 기대값이 합리적인가?
4. 팀 B 투자 철학 "한 번 더 의심하고 들어간다" 기준으로 통과 가능한가?

반드시 JSON만 출력하라:
{{
  "verdict": "승인/거절",
  "reason": "한 줄 근거",
  "risk_flags": ["리스크 항목 리스트, 없으면 빈 배열"]
}}
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


def analyze_with_deepseek_v3(stock_data: dict[str, Any]) -> str:
    """DeepSeek V3으로 종목 분석. 분석 결과 텍스트 반환."""
    try:
        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

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

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=1000,
        )

        result = response.choices[0].message.content.strip()
        logger.info("DeepSeek V3 분석 완료 | ticker=%s | result=%s", stock_data.get("ticker"), result[:200])
        return result

    except Exception as e:
        logger.error("DeepSeek V3 분석 실패 | ticker=%s | error=%s", stock_data.get("ticker"), e)
        raise


def decide_with_gemini_pro(analysis: str, stock_data: dict[str, Any]) -> dict[str, Any]:
    """Gemini Pro로 종목 결정."""
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        philosophy = _load_philosophy()
        system = SYSTEM_PROMPT_TEMPLATE.format(datetime=now, philosophy=philosophy)
        user = f"""DeepSeek V3의 분석 결과다:
{analysis}

위 분석을 검토하고 최종 판단을 내려라.
팀 B 투자 철학과 우선순위에 따라 판단하며 반드시 JSON만 출력하라."""

        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=f"{system}\n\n{user}",
        )
        raw = (response.text or "").strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        logger.info("Gemini Pro 결정 완료 | ticker=%s | action=%s", stock_data.get("ticker"), result.get("action"))
        return result

    except Exception as e:
        logger.error("Gemini Pro 결정 실패 | ticker=%s | error=%s", stock_data.get("ticker"), e)
        raise


def verify_with_deepseek_r1(gemini_decision: dict[str, Any], stock_data: dict[str, Any]) -> dict[str, Any]:
    """DeepSeek R1으로 반론 검증."""
    try:
        client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt = VERIFICATION_PROMPT.format(
            datetime=now,
            gemini_decision=json.dumps(gemini_decision, ensure_ascii=False, indent=2),
        )

        response = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        logger.info("DeepSeek R1 검증 완료 | ticker=%s | verdict=%s", stock_data.get("ticker"), result.get("verdict"))
        return result

    except Exception as e:
        logger.error("DeepSeek R1 검증 실패 | ticker=%s | error=%s", stock_data.get("ticker"), e)
        raise


def final_decision(gemini_result: dict[str, Any], r1_result: dict[str, Any]) -> dict[str, Any]:
    """Gemini Pro + R1 결과 종합. 둘 다 매수/승인이어야 매수 집행."""
    gemini_action = gemini_result.get("action", "관망")
    r1_verdict = r1_result.get("verdict", "거절")

    if gemini_action == "매수" and r1_verdict == "승인":
        final = gemini_result.copy()
        final["verification"] = "통과"
        final["risk_flags"] = r1_result.get("risk_flags", [])
    else:
        final = gemini_result.copy()
        final["action"] = "관망"
        final["verification"] = "거절"
        final["risk_flags"] = r1_result.get("risk_flags", [])
        final["rejection_reason"] = r1_result.get("reason", "R1 검증 거절")
        logger.info("팀 B 매수 거절 | gemini=%s | r1=%s | reason=%s",
                    gemini_action, r1_verdict, r1_result.get("reason"))

    return final


def run(stock_data: dict[str, Any]) -> dict[str, Any]:
    """팀 B 파이프라인 실행. stock_data를 받아 최종 결정 dict 반환."""
    ticker = stock_data.get("ticker", "")
    logger.info("팀 B 파이프라인 시작 | ticker=%s", ticker)

    try:
        # 1. DeepSeek V3 분석
        analysis = analyze_with_deepseek_v3(stock_data)

        # 2. Gemini Pro + DeepSeek R1 병렬 실행
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_gemini = executor.submit(decide_with_gemini_pro, analysis, stock_data)
            future_r1 = executor.submit(verify_with_deepseek_r1, json.loads(
                analysis.replace("```json", "").replace("```", "").strip()
            ) if analysis.strip().startswith("{") else {"action": "매수", "reason": analysis},
            stock_data)

            gemini_result = future_gemini.result()
            r1_result = future_r1.result()

        # 3. 최종 승인/거절 판단
        final = final_decision(gemini_result, r1_result)
        final["team"] = "B"
        final["decided_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info("팀 B 파이프라인 완료 | ticker=%s | action=%s | verification=%s",
                    ticker, final.get("action"), final.get("verification"))
        return final

    except Exception as e:
        logger.error("팀 B 파이프라인 실패 | ticker=%s | error=%s", ticker, e)
        return {
            "team": "B",
            "action": "관망",
            "ticker": ticker,
            "reason": f"파이프라인 오류: {e}",
            "confidence": "LOW",
            "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

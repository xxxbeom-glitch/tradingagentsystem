import os

BASE = os.path.dirname(__file__)

FOLDERS = [
    "logs/api",
    "logs/market",
    "logs/agents",
    "logs/portfolio",
    "logs/system",
    "reports",
]

LOG_FILES = {
    "logs/api/kis_api.log":        "# 한국투자증권 API 호출/응답 로그\n",
    "logs/api/dart_api.log":       "# DART API 호출/응답 로그\n",
    "logs/api/gemini_api.log":     "# Gemini API 호출/응답 로그\n",
    "logs/api/deepseek_api.log":   "# DeepSeek API 호출/응답 로그\n",
    "logs/api/naver_api.log":      "# Naver API 호출/응답 로그\n",
    "logs/api/pykrx_api.log":      "# pykrx 호출/응답 로그\n",
    "logs/market/realtime.log":    "# 실시간 시세 수신 로그\n",
    "logs/market/trigger.log":     "# 트리거 발동/미발동 로그\n",
    "logs/market/price_verify.log":"# AI 희망매수가 vs 실제 체결가 비교 로그\n",
    "logs/agents/team_a.log":      "# 팀 A 에이전트 판단 로그\n",
    "logs/agents/team_b.log":      "# 팀 B 에이전트 판단 로그\n",
    "logs/agents/verification.log":"# 팀 B DeepSeek R1 검증 단계 로그\n",
    "logs/portfolio/orders.log":   "# 매수/매도 주문 로그\n",
    "logs/portfolio/pnl.log":      "# 손익 기록 로그\n",
    "logs/system/error.log":       "# 시스템 에러 로그\n",
    "logs/system/health.log":      "# API 상태 체크 로그\n",
}

REPORT_FILES = {
    "reports/week1.md":  "# 1주차 리포트\n\n> 아직 생성되지 않았습니다.\n",
    "reports/week2.md":  "# 2주차 리포트\n\n> 아직 생성되지 않았습니다.\n",
    "reports/week3.md":  "# 3주차 리포트\n\n> 아직 생성되지 않았습니다.\n",
    "reports/final.md":  "# 최종 리포트\n\n> 아직 생성되지 않았습니다.\n",
}

def init_folders():
    print("폴더 및 파일 구조 초기화 시작\n")

    for folder in FOLDERS:
        path = os.path.join(BASE, folder)
        os.makedirs(path, exist_ok=True)
        print(f"  ✓ {folder}/")

    print()

    for filepath, content in {**LOG_FILES, **REPORT_FILES}.items():
        full_path = os.path.join(BASE, filepath)
        if not os.path.exists(full_path):
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"  ✓ {filepath} 생성")
        else:
            print(f"  — {filepath} 이미 존재")

    print("\n폴더 구조 초기화 완료")
    print("""
tradingagentsystem/
├── logs/
│   ├── api/
│   │   ├── kis_api.log
│   │   ├── dart_api.log
│   │   ├── gemini_api.log
│   │   ├── deepseek_api.log
│   │   ├── naver_api.log
│   │   └── pykrx_api.log
│   ├── market/
│   │   ├── realtime.log
│   │   ├── trigger.log
│   │   └── price_verify.log
│   ├── agents/
│   │   ├── team_a.log
│   │   ├── team_b.log
│   │   └── verification.log
│   ├── portfolio/
│   │   ├── orders.log
│   │   └── pnl.log
│   └── system/
│       ├── error.log
│       └── health.log
└── reports/
    ├── week1.md
    ├── week2.md
    ├── week3.md
    └── final.md
    """)

if __name__ == "__main__":
    init_folders()

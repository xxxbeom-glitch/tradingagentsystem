"""한국투자증권 Open API 실시간 데이터 전용 모듈."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable

import requests
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(ROOT_DIR, "logs", "api", "kis_api.log")
MARKET_LOG_PATH = os.path.join(ROOT_DIR, "logs", "market", "realtime.log")

DEFAULT_BASE_URL = "https://openapi.koreainvestment.com:9443"
DEFAULT_WS_URL = "ws://ops.koreainvestment.com:21000"


def _setup_logger(name: str, path: str) -> logging.Logger:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


api_logger = _setup_logger("kis_api", LOG_PATH)
rt_logger = _setup_logger("kis_realtime", MARKET_LOG_PATH)


class KISClient:
    """한국투자증권 Open API 클라이언트."""

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        base_url: str | None = None,
        ws_url: str | None = None,
    ) -> None:
        self.app_key = app_key or os.getenv("KIS_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET", "")
        self.base_url = (base_url or os.getenv("KIS_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.ws_url = ws_url or os.getenv("KIS_WS_URL", DEFAULT_WS_URL)
        self._access_token: str | None = None
        self._approval_key: str | None = None
        self._token_expires_at: float = 0.0
        self._is_vts = "openapivts" in self.base_url

    def _tr_id(self, real_id: str) -> str:
        if not self._is_vts:
            return real_id
        return "V" + real_id[1:] if real_id.startswith("F") else real_id

    def _headers(self, tr_id: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        token = self.get_token()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(tr_id),
                params=params,
                json=body,
                timeout=15,
            )
            data = response.json()
            api_logger.info(
                "%s %s | status=%s rt_cd=%s",
                method,
                path,
                response.status_code,
                data.get("rt_cd"),
            )
            if response.status_code != 200 or data.get("rt_cd") not in (None, "0"):
                msg = data.get("msg1") or data.get("error_description") or response.text
                raise RuntimeError(f"KIS API 오류: {msg}")
            return data
        except Exception as exc:
            api_logger.error("%s %s failed: %s", method, path, exc)
            raise

    def get_token(self, force: bool = False) -> str:
        """OAuth2 access_token 발급 (캐시)."""
        if (
            not force
            and self._access_token
            and time.time() < self._token_expires_at
        ):
            return self._access_token

        response = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=15,
        )
        data = response.json()
        api_logger.info("POST /oauth2/tokenP | status=%s", response.status_code)
        if response.status_code != 200 or "access_token" not in data:
            raise RuntimeError(f"토큰 발급 실패: {data}")

        self._access_token = data["access_token"]
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = time.time() + max(expires_in - 60, 60)
        return self._access_token

    def get_approval_key(self, force: bool = False) -> str:
        """WebSocket approval_key 발급."""
        if not force and self._approval_key:
            return self._approval_key

        response = requests.post(
            f"{self.base_url}/oauth2/Approval",
            headers={"content-type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            timeout=15,
        )
        data = response.json()
        api_logger.info("POST /oauth2/Approval | status=%s", response.status_code)
        if response.status_code != 200:
            raise RuntimeError(f"approval_key 발급 실패: {data}")

        self._approval_key = data.get("approval_key")
        if not self._approval_key:
            raise RuntimeError(f"approval_key 없음: {data}")
        return self._approval_key

    def get_current_price(self, ticker: str, market_div: str = "J") -> dict[str, Any]:
        """주식 현재가 조회."""
        code = str(ticker).zfill(6)
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            self._tr_id("FHKST01010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": market_div,
                "FID_INPUT_ISCD": code,
            },
        )
        output = data.get("output") or {}
        return {
            "ticker": code,
            "name": output.get("hts_kor_isnm") or output.get("prdt_name"),
            "price": int(output.get("stck_prpr") or 0),
            "change_rate": float(output.get("prdy_ctrt") or 0),
            "volume": int(output.get("acml_vol") or 0),
            "high": int(output.get("stck_hgpr") or 0),
            "low": int(output.get("stck_lwpr") or 0),
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "raw": output,
        }

    def get_investor_trend(self, ticker: str, market_div: str = "J") -> dict[str, Any]:
        """종목별 투자자 매매동향 (외국인/기관/개인)."""
        code = str(ticker).zfill(6)
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            self._tr_id("FHPTJ04160001"),
            params={
                "FID_COND_MRKT_DIV_CODE": market_div,
                "FID_INPUT_ISCD": code,
            },
        )
        rows = data.get("output") or []
        if isinstance(rows, dict):
            rows = [rows]
        return {
            "ticker": code,
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
        }

    def get_volume_rank(
        self,
        market: str = "J",
        top_n: int = 30,
    ) -> list[dict[str, Any]]:
        """거래량 순위 (한투 API)."""
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/volume-rank",
            self._tr_id("FHPST01710000"),
            params={
                "FID_COND_MRKT_DIV_CODE": market,
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": str(top_n),
                "FID_INPUT_DATE_1": "0",
            },
        )
        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]

        result = []
        for row in output[:top_n]:
            result.append(
                {
                    "ticker": str(row.get("mksc_shrn_iscd", "")).zfill(6),
                    "name": row.get("hts_kor_isnm") or row.get("data_rank_name"),
                    "price": int(row.get("stck_prpr") or 0),
                    "volume": int(row.get("acml_vol") or 0),
                    "change_rate": float(row.get("prdy_ctrt") or 0),
                    "foreign_net": int(row.get("frgn_ntby_qty") or 0),
                    "institution_net": int(row.get("orgn_ntby_qty") or 0),
                }
            )
        return result

    def get_net_buy_aggregate(self, ticker: str = "005930", market_div: str = "J") -> dict[str, Any]:
        """외국인/기관 순매수 (종목별 투자자 동향 기준)."""
        code = str(ticker).zfill(6)
        data = self._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            self._tr_id("FHKST01010900"),
            params={
                "FID_COND_MRKT_DIV_CODE": market_div,
                "FID_INPUT_ISCD": code,
            },
        )
        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]

        # 가장 최근 데이터 1건 기준
        row = output[0] if output else {}
        return {
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ticker": code,
            "foreign_net": int(row.get("frgn_ntby_qty") or row.get("frgn_ntby_tr_pbmn") or 0),
            "institution_net": int(row.get("orgn_ntby_qty") or row.get("orgn_ntby_tr_pbmn") or 0),
            "raw": row,
        }

    def get_news_titles(self, ticker: str, count: int = 10) -> list[dict[str, Any]]:
        """종목 뉴스 제목 조회."""
        code = str(ticker).zfill(6)
        try:
            data = self._request(
                "GET",
                "/uapi/domestic-stock/v1/quotations/news-title",
                self._tr_id("FHKST01011800"),
                params={
                    "FID_NEWS_DT": datetime.now().strftime("%Y%m%d"),
                    "FID_NEWS_TM": "",
                    "FID_NEWS_SEQ": "",
                    "FID_INPUT_ISCD": code,
                    "FID_NEWS_CNT": str(count),
                },
            )
        except Exception:
            # 일부 환경(VTS)에서 미지원일 수 있음 — 빈 목록 반환
            api_logger.warning("news-title API unavailable for %s", code)
            return []

        output = data.get("output") or []
        if isinstance(output, dict):
            output = [output]

        news = []
        for row in output[:count]:
            news.append(
                {
                    "ticker": code,
                    "title": (row.get("news_titl") or row.get("hts_pbnt_titl_cntt") or "").strip(),
                    "published_at": row.get("news_dt") or row.get("data_dt"),
                    "source": row.get("news_oso_name") or row.get("dorg"),
                }
            )
        return news

    def subscribe_realtime(
        self,
        tickers: list[str],
        on_message: Callable[[str, str], None],
        duration_sec: int = 30,
    ) -> None:
        """WebSocket 실시간 체결 구독 (H0STCNT0)."""
        try:
            import websocket  # type: ignore
        except ImportError as exc:
            raise ImportError("websocket-client 패키지가 필요합니다.") from exc

        approval = self.get_approval_key()
        codes = [str(t).zfill(6) for t in tickers]

        def _on_open(ws: Any) -> None:
            for code in codes:
                payload = {
                    "header": {
                        "approval_key": approval,
                        "custtype": "P",
                        "tr_type": "1",
                        "content-type": "utf-8",
                    },
                    "body": {
                        "input": {
                            "tr_id": "H0STCNT0",
                            "tr_key": code,
                        }
                    },
                }
                ws.send(json.dumps(payload))
                rt_logger.info("subscribe H0STCNT0.%s", code)

        def _on_message_handler(_ws: Any, message: str) -> None:
            rt_logger.info("tick | %s", message[:200])
            on_message("H0STCNT0", message)

        def _on_error(_ws: Any, error: Any) -> None:
            api_logger.error("websocket error: %s", error)

        ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=_on_open,
            on_message=_on_message_handler,
            on_error=_on_error,
        )

        thread = threading.Thread(
            target=lambda: ws.run_forever(ping_interval=20, ping_timeout=10),
            daemon=True,
        )
        thread.start()
        time.sleep(duration_sec)
        ws.close()
        thread.join(timeout=5)


# 모듈 레벨 편의 함수
_default_client: KISClient | None = None


def _client() -> KISClient:
    global _default_client
    if _default_client is None:
        _default_client = KISClient()
    return _default_client


def get_token(force: bool = False) -> str:
    return _client().get_token(force=force)


def get_current_price(ticker: str) -> dict[str, Any]:
    return _client().get_current_price(ticker)


def get_investor_trend(ticker: str) -> dict[str, Any]:
    return _client().get_investor_trend(ticker)


def get_volume_rank(top_n: int = 30) -> list[dict[str, Any]]:
    return _client().get_volume_rank(top_n=top_n)


def get_net_buy_aggregate() -> dict[str, Any]:
    return _client().get_net_buy_aggregate()


def get_news_titles(ticker: str, count: int = 10) -> list[dict[str, Any]]:
    return _client().get_news_titles(ticker, count=count)


def subscribe_realtime(
    tickers: list[str],
    on_message: Callable[[str, str], None],
    duration_sec: int = 30,
) -> None:
    _client().subscribe_realtime(tickers, on_message, duration_sec=duration_sec)

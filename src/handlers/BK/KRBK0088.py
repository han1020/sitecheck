"""
KRBK0088 - 신한은행 (기업뱅킹 새소식)

흐름 (JSON POST API):
  1. GET https://bizbank.shinhan.com/    → 워밍업
  2. POST https://bizbank.shinhan.com/serviceEndpoint/httpDigital
       Content-Type: application/json
       Body: { dataBody, dataHeader(trxCd=CSBZA0100E15) }
     응답 JSON: { dataBody: { grid01: [ { ttlS1000, ctntS4000, drDttm } ] } }
  3. ctntS4000 이 상세 HTML을 직접 포함 → 추가 호출 불필요
"""
from __future__ import annotations

import json
import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://bizbank.shinhan.com"
API_PATH = "/serviceEndpoint/httpDigital"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 워밍업
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0088] 워밍업 실패(무시): {e}")

    # 2) JSON API 호출
    max_items = getattr(site_config, "max_items", 15)
    payload = {
        "dataBody": {
            "srchGS1": "0",
            "srchNmS1000": "",
            "srnN5": 1,
            "ernN5": max_items,
            "pageGS10": "page",
            "comPermitmultitransactionS5": "true",
        },
        "dataHeader": {
            "trxCd": "CSBZA0100E15",
            "globId": "", "reqMsgIlsi": "", "outMsgIlsi": "",
            "language": "1", "subChannel": "",
            "result": "", "resultCode": "",
            "resultMsg": None, "resultDetail": None,
            "channelGbn": "E0", "submitGbn": "", "programId": "BCO270101_W101",
        },
    }
    s.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    })
    try:
        r = s.post(HOST + API_PATH, data=json.dumps(payload), timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0088] API 호출 실패: {e}")
        return []

    header = data.get("dataHeader") or {}
    if header.get("result") != "SUCCESS":
        logger.warning(
            f"[KRBK0088] API 비정상 응답: result={header.get('result')}, "
            f"code={header.get('resultCode')}, msg={header.get('resultMsg')}"
        )
        return []

    grid = (data.get("dataBody") or {}).get("grid01") or []
    results: List[HandlerResult] = []

    for it in grid:
        title = (it.get("ttlS1000") or "").strip()
        if not title:
            continue
        drdt = (it.get("drDttm") or "").strip()
        # YYYYMMDDHHMMSS → YYYYMMDD
        posted_date = drdt[:8] if len(drdt) >= 8 else drdt
        contents_html = it.get("ctntS4000") or ""

        try:
            soup = BeautifulSoup(contents_html, "lxml")
            for st in soup.find_all("style"):
                st.decompose()
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = contents_html

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + API_PATH,
            detail_html=contents_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0088] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0088", handle)

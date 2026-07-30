"""
KRBK0002 - 산업은행 (KDB 공지사항)

흐름 (JSON API + 본문 응답 포함):
  1. GET https://www.kdb.co.kr/                                       → 쿠키
  2. POST https://www.kdb.co.kr/BOUBUF01R01.jct
       Content-Type: application/x-www-form-urlencoded
       Body: _JSON_=<double-url-encoded JSON>
     응답 JSON.STDLIST_REC: [ { NAC_CONE_TTL, FST_ENR_DTM, NAC_HTML_TXT_INF } ]
"""
from __future__ import annotations

import json
import logging
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.kdb.co.kr"
API_PATH = "/BOUBUF01R01.jct"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0002] 워밍업 실패(무시): {e}")

    max_items = getattr(site_config, "max_items", 15)
    post_obj = {
        "SEARCH_CONDITION": "", "SEARCH_KEYWORD": "", "SEARCH_CATEGORY": "",
        "ORDER_TYPE": "new", "MODE": "", "ITR_BLB_ID": "STD119",
        "HEADER_STD_WEB": {
            "REQ_PAGE_NO": 1,
            "PAGE_ROW_COUNT": str(max_items),
            "NEXT_PAGE_YN": "S", "NEXTPAGDTT": "P",
            "GRID_NEXTKEY_ITR_CND": "",
        },
    }
    body = "_JSON_=" + quote(quote(json.dumps(post_obj), safe=""), safe="")
    s.headers.update({
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
    })

    try:
        r = s.post(HOST + API_PATH, data=body, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0002] API 호출 실패: {e}")
        return []

    items = data.get("STDLIST_REC") or []
    results: List[HandlerResult] = []

    for it in items[:max_items]:
        title = (it.get("NAC_CONE_TTL") or "").strip()
        if not title:
            continue
        dtm = (it.get("FST_ENR_DTM") or "").strip()
        # FST_ENR_DTM은 YYYYMMDDHHMMSS → YYYYMMDD
        posted_date = dtm[:8] if len(dtm) >= 8 else dtm
        contents_html = it.get("NAC_HTML_TXT_INF") or ""

        try:
            soup = BeautifulSoup(contents_html, "lxml")
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

    logger.info(f"[KRBK0002] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0002", handle)

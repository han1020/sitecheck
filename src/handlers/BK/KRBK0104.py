"""
KRBK0104 - 애큐온저축은행 (공지사항)

흐름 (JSON API, _JSON_= 이중 URL 인코딩, 원본 스크래퍼 그대로 포팅):
  1. GET /sv_hki0040110.act  → 쿠키
  2. POST /sd_TUB_NEWS_ANNC_R001.jct
       body: _JSON_=<이중인코딩({"USE_YN":"Y","PAGE_LINE":"10","CON_TR_NO":"1"})>
     → REC_001[]  (NEWS_ANNC_SEQ/NEWS_ANNC_TIT/REG_DATE/ORG_C)
  3. POST /sd_TUB_NEWS_ANNC_R002.jct
       body: _JSON_=<이중인코딩({"INQ_TC":"1","ORG_C":<org>,"NEWS_ANNC_SEQ":<seq>})>
     → NEWS_ANNC_CNTN (상세 HTML)
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

HOST = "https://www.acuonsb.co.kr"
WARMUP = HOST + "/sv_hki0040110.act"
LIST_URL = HOST + "/sd_TUB_NEWS_ANNC_R001.jct"
DETAIL_URL = HOST + "/sd_TUB_NEWS_ANNC_R002.jct"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
# JS encodeURIComponent 가 인코딩하지 않는 unreserved 문자
_JS_SAFE = "-_.!~*'()"


def _enc2(obj) -> str:
    """encodeURIComponent(encodeURIComponent(JSON.stringify(obj)))."""
    once = quote(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), safe=_JS_SAFE)
    return quote(once, safe=_JS_SAFE)


def _text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for st in soup.find_all("style"):
        st.decompose()
    return soup.get_text("\n", strip=True)


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    try:
        s.get(WARMUP, timeout=20)
        s.headers.update({
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        })
        list_body = "_JSON_=" + _enc2({"USE_YN": "Y", "PAGE_LINE": "10", "CON_TR_NO": "1"})
        r = s.post(LIST_URL, data=list_body, timeout=20)
        r.raise_for_status()
        items = r.json().get("REC_001") or []
    except Exception as e:
        logger.warning(f"[KRBK0104] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in items:
        title = (it.get("NEWS_ANNC_TIT") or "").strip()
        if not title:
            continue
        posted_date = str(it.get("REG_DATE") or "").strip()[:8]
        seq = it.get("NEWS_ANNC_SEQ")
        org = it.get("ORG_C")

        try:
            body = "_JSON_=" + _enc2({"INQ_TC": "1", "ORG_C": org, "NEWS_ANNC_SEQ": seq})
            dr = s.post(DETAIL_URL, data=body, timeout=20)
            dr.raise_for_status()
            content_html = dr.json().get("NEWS_ANNC_CNTN") or ""
        except Exception as e:
            logger.debug(f"[KRBK0104] 상세 실패(seq={seq}): {e}")
            continue
        if not content_html.strip():
            continue

        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=DETAIL_URL,
            detail_html=content_html, body_text=_text(content_html),
        ))

    logger.info(f"[KRBK0104] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0104", handle)

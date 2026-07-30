"""
KRBK0113 - 신한저축은행 (공지사항)

흐름 (JSON API, 원본 스크래퍼 그대로 포팅):
  1. POST /api/SC0009/selectNewsCtg.json  {"pageNum":1,"srchPostCagCd":""}
       → data.data.mtxTt[]  (postTil/regDtime/postSeq)
  2. POST /api/SC0010/selectNewsDl.json  {"schPostSeq":<postSeq>}
       → data.data.dtlInfo.postTt (상세 HTML)
"""
from __future__ import annotations

import json
import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.shinhansavings.com"
LIST_URL = HOST + "/api/SC0009/selectNewsCtg.json"
DETAIL_URL = HOST + "/api/SC0010/selectNewsDl.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)


def _text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for st in soup.find_all("style"):
        st.decompose()
    return soup.get_text("\n", strip=True)


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    })
    try:
        r = s.post(LIST_URL, data=json.dumps({"pageNum": 1, "srchPostCagCd": ""}), timeout=20)
        r.raise_for_status()
        items = ((r.json().get("data") or {}).get("mtxTt")) or []
    except Exception as e:
        logger.warning(f"[KRBK0113] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in items:
        title = (it.get("postTil") or "").strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-/]", "", str(it.get("regDtime") or "")).strip()[:8]
        seq = it.get("postSeq")

        try:
            dr = s.post(DETAIL_URL, data=json.dumps({"schPostSeq": seq}), timeout=20)
            dr.raise_for_status()
            dtl = ((dr.json().get("data") or {}).get("dtlInfo")) or {}
            content_html = dtl.get("postTt") or ""
        except Exception as e:
            logger.debug(f"[KRBK0113] 상세 실패(seq={seq}): {e}")
            continue
        if not content_html.strip():
            continue

        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=DETAIL_URL,
            detail_html=content_html, body_text=_text(content_html),
        ))

    logger.info(f"[KRBK0113] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0113", handle)

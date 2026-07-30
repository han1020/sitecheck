"""
KRBK0023 - SC제일은행 (Standard Chartered Korea)

흐름 (2026 기준 신 사이트):
  페이지: https://www.standardchartered.co.kr/np/kr/cm/cm/IndexNoticeBoard2.jsp?DtlId=2
    → 페이지 JS 가 DtlId=2 를 Did="CM1002" 로 매핑하고 LOCTN_CD="LN1001" 로 설정

  POST https://www.standardchartered.co.kr/np/kr/boardList2
    Content-Type: application/json
    Body: {"serviceID":"HP_CM_CM_IndexNew.getNewList2",
           "DTL_LOCTN_CD":"CM1002", "LOCTN_CD":"LN1001", ...}
  응답 JSON.vector: [ { New: { TITLE, S_DATE, SUMMARY(HTML) } } ]
"""
from __future__ import annotations

import html
import json
import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.standardchartered.co.kr"
PAGE_PATH = "/np/kr/cm/cm/IndexNoticeBoard2.jsp?DtlId=2"
API_PATH = "/np/kr/boardList2"
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
    # 페이지 GET (쿠키 + Referer 확보)
    try:
        s.get(HOST + PAGE_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0023] 페이지 GET 실패(무시): {e}")

    s.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": HOST + PAGE_PATH,
    })

    body = json.dumps({
        "serviceID": "HP_CM_CM_IndexNew.getNewList2",
        "DTL_LOCTN_CD": "CM1002",
        "LOCTN_CD": "LN1001",
        "SEQNO": "", "boardList": "",
        "OFFCL_ITM_CTGRY": "", "keyword": "", "d_flag": "",
    })

    try:
        r = s.post(HOST + API_PATH, data=body, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0023] API 호출 실패: {e}")
        return []

    vector = data.get("vector") or []
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for entry in vector[:max_items]:
        new = entry.get("New") if isinstance(entry, dict) else None
        if not new:
            continue
        title = (new.get("TITLE") or "").strip()
        if not title:
            continue
        sdate = (new.get("S_DATE") or "").strip()
        # 'YYYY-MM-DD' → 'YYYYMMDD'
        posted_date = sdate.replace("-", "")

        # 본문 HTML: 2026 신 API는 CONTENTS, 구 API는 SUMMARY
        contents_html = new.get("CONTENTS") or new.get("SUMMARY") or ""
        contents_html = html.unescape(contents_html)

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

    logger.info(f"[KRBK0023] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0023", handle)

"""
KRBK0106 - KB저축은행 (공지사항, WebSquare)

흐름 (JSON API, 본문이 목록 응답에 포함, 원본 스크래퍼 그대로 포팅):
  1. POST /websquare/engine/callJsonService.jsp?serviceID=S_CommonBBSService_selectPostList
       header: submissionid=selectPostList, Content-Type application/json
       body: {"SEARCH":{"BRD_ID":"1",...,"PAGE_ROW":"10",...}}
     → DATA.RESULT_LIST[]  (TITLE/REG_DATE/CONTENT_HTML)
  상세 호출 없음 - CONTENT_HTML 이 본문
"""
from __future__ import annotations

import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.kbsavings.com"
LIST_URL = HOST + "/websquare/engine/callJsonService.jsp?serviceID=S_CommonBBSService_selectPostList"
REFERER = HOST + "/websquare/websquare.jsp?w2xPath=/jsp/companyMng/prCenter/noticeList.xml"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_PAYLOAD = (
    '{"SEARCH":{"BRD_ID":"1","POST_ID":"","PAGING_INDEX":"1","PAGE_ROW":"10",'
    '"CATE_CODE":"","LOAN_CATE_CODE":"","SEARCH_TYPE":"","SEARCH_KEYWORD":""}}'
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
        "submissionid": "selectPostList",
        "Content-Type": 'application/json; charset="UTF-8"',
        "Accept": "application/json, text/plain, */*",
        "Referer": REFERER,
        "Connection": "keep-alive",
    })
    try:
        r = s.post(LIST_URL, data=_PAYLOAD.encode("utf-8"), timeout=20)
        r.raise_for_status()
        result = (r.json().get("DATA") or {}).get("RESULT_LIST") or []
    except Exception as e:
        logger.warning(f"[KRBK0106] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in result:
        title = (it.get("TITLE") or "").strip()
        if not title:
            continue
        posted_date = str(it.get("REG_DATE") or "").strip()[:8]
        content_html = it.get("CONTENT_HTML") or ""
        if not content_html.strip():
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=REFERER,
            detail_html=content_html, body_text=_text(content_html),
        ))

    logger.info(f"[KRBK0106] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0106", handle)

"""
KRBK0020 - 우리은행 (기업뱅킹 새소식)

흐름 (신 사이트 nbi.wooribank.com JSON API):
  1. GET https://nbi.wooribank.com                                  → 워밍업
  2. POST https://nbi.wooribank.com/nbi/jcc?withyou=BICOM0066&__ID=c052030
       Content-Type: application/json
       Body: { boardId, pageRowCnt, curPageNo, ... }
     응답 JSON: { bbsList: [ { title, regiDate, contents(HTML) } ] }
  3. contents 가 상세 HTML을 직접 포함 → 추가 GET 불필요
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

HOST = "https://nbi.wooribank.com"
API_PATH = "/nbi/jcc?withyou=BICOM0066&__ID=c052030"
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

    # 1) 워밍업 (쿠키)
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0020] 워밍업 실패(무시): {e}")

    # 2) JSON API
    payload = {
        "categoryId": "", "boardId": "B00517",
        "pageRowCnt": str(getattr(site_config, "max_items", 15)),
        "curPageNo": "1",
        "searchField": "TITLE_CONTENTS",
        "articleId": "", "no": "", "no2": "", "keyword": "",
        "orderType": "",
        "listPageId": "BIBKM0088", "detailPageId": "BIBKM0088",
        "attYn": "Y",
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
        logger.warning(f"[KRBK0020] API 호출 실패: {e}")
        return []

    items = data.get("bbsList") or []
    results: List[HandlerResult] = []

    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        regi = (it.get("regiDate") or "").strip()
        # 'YYYY.MM.DD' → 'YYYYMMDD'
        posted_date = regi.replace(".", "").replace("-", "")
        contents_html = it.get("contents") or ""

        # HTML → text
        try:
            soup = BeautifulSoup(contents_html, "lxml")
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = contents_html

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + API_PATH,  # 별도 상세 URL 없음, API endpoint로 대체
            detail_html=contents_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0020] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0020", handle)

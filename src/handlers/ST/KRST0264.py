"""
KRST0264 - 키움증권 (Kiwoom Securities)

흐름 (원본 JS 스크래퍼 그대로 포팅):
  1. GET  https://www1.kiwoom.com/h/main                              → 쿠키
  2. POST https://bbn.kiwoom.com/bbs/SBbsNoticeNWWNZListAjax
       Headers: X-Requested-With=XMLHttpRequest,
                Accept='*/*;',
                Content-Type='application/x-www-form-urlencoded; charset=UTF-8'
       Body:    pageNo=1&divTp1=&f_keyField=ttl&f_key=&cd=0&_reqAgent=ajax
     응답 JSON.noticeList: [ {ttl, makeTime, seqid, ...}, ... ]
  3. 각 공지마다 POST https://bbn.kiwoom.com/bbs/SBbsNoticeNWWNZDetailAjax
       Headers: Accept='text/html,...', Content-Type='application/x-www-form-urlencoded'
                (X-Requested-With 제거)
       Body:    seqid={seqid}&_reqAgent=ajax
     응답 JSON.noticeDetail.dtlCntn 의 HTML 을 파싱
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup, Comment

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST_MAIN = "https://www1.kiwoom.com"
HOST_BBN = "https://bbn.kiwoom.com"
LIST_URL = f"{HOST_BBN}/bbs/SBbsNoticeNWWNZListAjax"
DETAIL_URL = f"{HOST_BBN}/bbs/SBbsNoticeNWWNZDetailAjax"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

LIST_BODY = "pageNo=1&divTp1=&f_keyField=ttl&f_key=&cd=0&_reqAgent=ajax"

# 본문에서 제거할 selector 목록
_REMOVE_SELECTORS = [
    "script",
    "#quick",
    "#snsShareQ",
    "#frmCnd",
    "div#footer",
    "#accNav",
    "#gnb",
    "div.lineMapWrap",
    "#lnb",
    "div.pageOptWrap",
    "div.boardPager",
]


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
        "Connection": "keep-alive",
    })
    return s


def _normalize_date(raw: str) -> str:
    """'2024.10.07' / '2024-10-07' / '2024.10.07 12:34' → '20241007'."""
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    return digits[:8] if len(digits) >= 8 else digits


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 메인 페이지로 쿠키 획득
    try:
        s.get(f"{HOST_MAIN}/h/main", timeout=20)
    except Exception as e:
        logger.debug(f"[KRST0264] 메인 페이지 호출 실패(무시): {e}")

    # 2) 목록 POST
    list_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*;",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        r = s.post(LIST_URL, data=LIST_BODY, headers=list_headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRST0264] 목록 API 실패: {e}")
        return []

    notice_list = data.get("noticeList") or []
    if not notice_list:
        logger.warning("[KRST0264] noticeList 비어있음")
        return []

    detail_headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
    }

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for ele in notice_list[:max_items]:
        if not isinstance(ele, dict):
            continue

        title = (ele.get("ttl") or "").strip()
        seqid = (ele.get("seqid") or "").strip() if isinstance(ele.get("seqid"), str) \
            else str(ele.get("seqid") or "").strip()
        posted_date = _normalize_date(str(ele.get("makeTime") or ""))

        if not title or not seqid:
            continue

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(
                DETAIL_URL,
                data=f"seqid={seqid}&_reqAgent=ajax",
                headers=detail_headers,
                timeout=20,
            )
            dr.raise_for_status()
            dt_json = dr.json()
            dtl_html = (
                (dt_json.get("noticeDetail") or {}).get("dtlCntn") or ""
            )
        except Exception as e:
            logger.debug(f"[KRST0264] 상세 조회 실패(seqid={seqid}): {e}")
            dtl_html = ""

        if dtl_html:
            d_soup = BeautifulSoup(dtl_html, "lxml")
            for sel in _REMOVE_SELECTORS:
                for tag in d_soup.select(sel):
                    tag.decompose()
            # HTML 주석 제거
            for c in d_soup.find_all(string=lambda t: isinstance(t, Comment)):
                c.extract()

            detail_html = str(d_soup).strip()
            body_text = d_soup.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=f"{DETAIL_URL}?seqid={seqid}",
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0264] 핸들러 추출 {len(results)}건")
    return results


register("KRST0264", handle)

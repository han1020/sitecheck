"""
KRST0240 - 삼성증권 (Samsung Securities)

흐름:
  1) GET https://www.samsungpop.com/ux/kor/customer/notice/notice/noticeList.do
     → 세션 쿠키 확보 (requests.Session 이 자동 보관)
  2) POST https://www.samsungpop.com/ux/kor/customer/notice/notice/getNoticeList.do
     form-encoded body:
       currentPage=1&rowsPerPage=40&Search=1&SearchText=&searchStartDate=
       &searchEndDate=&tabIndex=0&searchType=0&siteGubun=KF
       &af_auto_index=start&ajaxQuery=1
     응답 JSON.data.list = [ { NO, ntcTitle1, menuSeqNo, procDTime2 } ]
  3) 상세: GET /ux/kor/customer/notice/notice/noticeViewContent.do?MenuSeqNo={menuSeqNo}
     → <body> HTML 추출, script/style 제거 후 body_text 생성
"""
from __future__ import annotations

import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.samsungpop.com"
LIST_PAGE_PATH = "/ux/kor/customer/notice/notice/noticeList.do"
LIST_API_PATH = "/ux/kor/customer/notice/notice/getNoticeList.do"
DETAIL_PATH = "/ux/kor/customer/notice/notice/noticeViewContent.do"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 리스트 페이지 GET → 쿠키 확보
    try:
        s.get(HOST + LIST_PAGE_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0240] 리스트 페이지 GET 실패(무시): {e}")

    # 2) 목록 API POST
    form_body = (
        "currentPage=1"
        "&rowsPerPage=40"
        "&Search=1"
        "&SearchText="
        "&searchStartDate="
        "&searchEndDate="
        "&tabIndex=0"
        "&searchType=0"
        "&siteGubun=KF"
        "&af_auto_index=start"
        "&ajaxQuery=1"
    )

    api_headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": HOST + LIST_PAGE_PATH,
        "Origin": HOST,
    }

    try:
        r = s.post(
            HOST + LIST_API_PATH,
            data=form_body,
            headers=api_headers,
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning(f"[KRST0240] 목록 API 호출 실패: {e}")
        return []

    # 응답 형태: {"totalCount": N, "list": [...], "info": {...}}
    notice_list = payload.get("list") if isinstance(payload, dict) else []
    notice_list = notice_list or []

    results: List[HandlerResult] = []

    for item in notice_list:
        if len(results) >= max_items:
            break
        if not isinstance(item, dict):
            continue

        title = str(item.get("ntcTitle1") or "").strip()
        menu_seq_no = str(item.get("menuSeqNo") or "").strip()
        raw_date = str(item.get("procDTime2") or "").strip()
        posted_date = raw_date.replace("-", "").strip()

        if not title or not menu_seq_no:
            continue

        detail_url = f"{HOST}{DETAIL_PATH}?MenuSeqNo={menu_seq_no}"

        try:
            dr = s.get(detail_url, timeout=15)
            dr.raise_for_status()
        except Exception as e:
            logger.warning(f"[KRST0240] 상세 페이지 GET 실패 ({menu_seq_no}): {e}")
            continue

        # 상세 본문 파싱
        try:
            soup = BeautifulSoup(dr.text, "lxml")
        except Exception:
            soup = BeautifulSoup(dr.text, "html.parser")

        for tag in soup(["script", "style"]):
            tag.decompose()

        body_tag = soup.body
        if body_tag is not None:
            detail_html = body_tag.decode_contents().strip()
            body_text = body_tag.get_text("\n", strip=True)
        else:
            detail_html = dr.text
            body_text = soup.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0240] 핸들러 추출 {len(results)}건")
    return results


register("KRST0240", handle)

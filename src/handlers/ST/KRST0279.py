"""
KRST0279 - DB금융투자 (DB Financial Investment)

흐름:
  1) GET https://www.dbsec.co.kr/custcenter/notices/cu_Notices_lst.do (초기 쿠키)
  2) POST https://www.dbsec.co.kr/appData/not_sub_lst.json (빈 body)
       응답 JSON.data: [ { id, title, rdt, ... } ]
  3) 상세: POST https://www.dbsec.co.kr/appData/descNot/{id}.json
       응답 JSON.data.body (HTML)
"""
from __future__ import annotations

import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.dbsec.co.kr"
LIST_PAGE_PATH = "/custcenter/notices/cu_Notices_lst.do"
LIST_API_PATH = "/appData/not_sub_lst.json"
DETAIL_API_PATH = "/appData/descNot/{cn_id}.json"
REFERER = HOST + LIST_PAGE_PATH

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Connection": "keep-alive",
    })

    # 1) 초기 GET → 세션 쿠키 확보
    try:
        s.get(HOST + LIST_PAGE_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0279] 초기 GET 실패(무시): {e}")

    # 2) 목록 API POST (공지/일반 동일 엔드포인트)
    list_headers = {
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": HOST,
    }

    try:
        r = s.post(
            HOST + LIST_API_PATH,
            data="",
            headers=list_headers,
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        logger.warning(f"[KRST0279] 목록 API 호출 실패: {e}")
        return []

    grid = []
    if isinstance(payload, dict):
        data_field = payload.get("data")
        if isinstance(data_field, list):
            grid = data_field
        elif isinstance(data_field, dict):
            inner = data_field.get("data")
            if isinstance(inner, list):
                grid = inner

    if not grid:
        logger.warning("[KRST0279] 목록 비어있음")
        return []

    # 상세 호출용 헤더 (X-Requested-With 제거)
    detail_headers = {
        "Referer": REFERER,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": HOST,
    }

    results: List[HandlerResult] = []
    dupl_chk: set[str] = set()

    for ele in grid:
        if len(results) >= max_items:
            break
        if not isinstance(ele, dict):
            continue

        title = str(ele.get("title") or "").strip()
        posted_date = str(ele.get("rdt") or "").strip()
        cn_id = str(ele.get("id") or "").strip()

        if not cn_id or cn_id in dupl_chk:
            continue
        dupl_chk.add(cn_id)

        if not title or not posted_date:
            continue

        detail_url = HOST + DETAIL_API_PATH.format(cn_id=cn_id)

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(detail_url, data="", headers=detail_headers, timeout=15)
            dr.raise_for_status()
            dt_payload = dr.json()
        except Exception as e:
            logger.debug(f"[KRST0279] 상세 요청/파싱 실패({title[:30]}): {e}")
            continue

        # 응답 구조: { data: { body: "<html>", ... }, ... }
        body_html = ""
        if isinstance(dt_payload, dict):
            data_obj = dt_payload.get("data")
            if isinstance(data_obj, dict):
                body_html = str(data_obj.get("body") or "")

        if body_html:
            try:
                d_soup = BeautifulSoup(body_html, "lxml")
            except Exception:
                d_soup = BeautifulSoup(body_html, "html.parser")

            for tag in d_soup(["script"]):
                tag.decompose()

            body_tag = d_soup.body
            if body_tag is not None:
                detail_html = body_tag.decode_contents().strip()
            else:
                detail_html = str(d_soup).strip()

            body_text = d_soup.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0279] 핸들러 추출 {len(results)}건")
    return results


register("KRST0279", handle)

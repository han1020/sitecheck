"""
KRBK0081 - 하나은행 (기업 새소식/이벤트)

흐름:
  1. GET https://biz.kebhana.com                                    → 쿠키
  2. GET https://biz.kebhana.com/conts/index.do?goUrl=...&oid=020   → 메인 페이지 (워밍업)
  3. POST https://biz.kebhana.com/cont/customer/customer07/search.jsp
       body: Ctype=B&cid=view_main&oid=020                          → 공지 목록 HTML
  4. 각 행: a href 의 goUrl= 부분이 실제 콘텐츠 경로
  5. GET https://biz.kebhana.com<goUrl>                             → 상세 페이지
  6. div.table_view 안의 내용을 detail_html / body_text 로 사용
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://biz.kebhana.com"
MAIN_PATH = "/conts/index.do?goUrl=/cont/customer/customer07/index.jsp&Ctype=B&cid=view_main&oid=020"
LIST_AJAX = "/cont/customer/customer07/search.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _new_session() -> requests.Session:
    s = requests.Session()
    # biz.kebhana.com이 간헐적으로 응답 없이 연결을 끊음(RemoteDisconnected).
    # 목록 POST는 조회 전용이라 재시도해도 안전.
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 메인 + 메뉴 페이지로 세션 워밍업
    s.get(HOST, timeout=15)
    s.get(HOST + MAIN_PATH, timeout=15)

    # 2) 공지 목록 (POST ajax)
    s.headers["Referer"] = HOST + MAIN_PATH
    r = s.post(
        HOST + LIST_AJAX,
        data={"Ctype": "B", "cid": "view_main", "oid": "020"},
        timeout=15,
    )
    r.encoding = r.apparent_encoding or "utf-8"
    soup = BeautifulSoup(r.text, "lxml")

    rows = soup.select("table tr")
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []
    processed = 0

    for tr in rows:
        if processed >= max_items:
            break
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue  # 헤더 또는 비정상 행
        no = tds[0].get_text(strip=True)
        title_td = tds[1]
        a = title_td.find("a")
        if not a:
            continue
        title = title_td.get_text(" ", strip=True)
        if not title:
            continue
        posted_date = tds[2].get_text(strip=True).replace("-", "")
        processed += 1

        # 상세 페이지 (goUrl= 뒤 경로를 직접 GET 해야 콘텐츠가 나옴)
        href = a.get("href", "")
        actual_path = href.split("goUrl=", 1)[1] if "goUrl=" in href else href
        detail_url = urljoin(HOST, actual_path)
        try:
            dr = s.get(detail_url, timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            view = d_soup.select_one("div.table_view")
            detail_html = str(view) if view else dr.text
            body_text = view.get_text("\n", strip=True) if view else ""
        except Exception as e:
            logger.warning(f"[KRBK0081] 상세 GET 실패: {e}")
            detail_html = ""
            body_text = ""

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0081] 핸들러 추출 {len(results)}건")
    return results


# 자동 등록
register("KRBK0081", handle)

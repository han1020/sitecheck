"""
KRBK0004 - 국민은행 (KB기업뱅킹 새소식)

흐름 (기존 Node 스크래퍼 포팅):
  1. GET https://obiz.kbstar.com           → 쿠키 획득
  2. GET https://obiz.kbstar.com/quics?page=C025030   → 공지 목록 페이지 HTML
  3. table.tbl_list tbody tr 파싱 → 제목/날짜/링크 파라미터
  4. form[name=formPage]의 input 모음 + 행 a href의 query 파라미터 병합
  5. POST <hostURL><formAction> → 상세 페이지 HTML
  6. dd.cont 내용을 detail_html, .text를 body_text로 반환
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import parse_qsl, urljoin, urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://obiz.kbstar.com"
LIST_PATH = "/quics?page=C025030"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 메인으로 쿠키 획득
    s.get(HOST, timeout=15)

    # 2) 목록 페이지
    list_url = HOST + LIST_PATH
    r = s.get(list_url, timeout=15)
    r.encoding = r.apparent_encoding or "utf-8"
    list_html = r.text
    soup = BeautifulSoup(list_html, "lxml")

    # form[name=formPage] 의 input 값 모으기
    form = soup.find("form", attrs={"name": "formPage"})
    if not form:
        logger.warning("[KRBK0004] form[name=formPage] 미발견")
        return []
    form_inputs = {
        (inp.get("name") or ""): (inp.get("value") or "")
        for inp in form.find_all("input")
        if inp.get("name")
    }
    form_action = form.get("action", "")
    if not form_action:
        logger.warning("[KRBK0004] formPage action 없음")
        return []

    # table.tbl_list 의 tr 파싱 (tbody가 없는 HTML)
    table = soup.select_one("table.tbl_list")
    if not table:
        logger.warning("[KRBK0004] table.tbl_list 미발견")
        return []
    rows = table.select("tr")

    results: List[HandlerResult] = []
    max_items = getattr(site_config, "max_items", 15)
    processed = 0

    for tr in rows:
        if processed >= max_items:
            break
        td_left = tr.select_one("td.left")
        if not td_left:
            # 헤더 행 또는 빈 행
            continue
        title = td_left.get_text(strip=True)
        if not title:
            continue
        processed += 1

        a = tr.find("a")
        param_data = (a.get("href") or "") if a else ""

        td_date = tr.select_one("td.date")
        posted_date = td_date.get_text(strip=True).replace(".", "") if td_date else ""

        # href 쿼리 → form input에 병합
        merged = dict(form_inputs)
        if "?" in param_data:
            qs = param_data.split("?", 1)[1]
            for k, v in parse_qsl(qs, keep_blank_values=True):
                merged[k] = v
        merged.pop("page", None)
        body = urlencode(merged)

        detail_url_post = urljoin(HOST, form_action)
        try:
            dr = s.post(
                detail_url_post,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            dr.encoding = dr.apparent_encoding or "utf-8"
            detail_html = dr.text
            d_soup = BeautifulSoup(detail_html, "lxml")
            dd_cont = d_soup.select_one("dd.cont")
            body_html = str(dd_cont) if dd_cont else detail_html
            body_text = (dd_cont.get_text("\n", strip=True) if dd_cont else "")
        except Exception as e:
            logger.warning(f"[KRBK0004] 상세 요청 실패: {e}")
            body_html = ""
            body_text = ""

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url_post,
            detail_html=body_html or detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0004] 핸들러 추출 {len(results)}건")
    return results


# 자동 등록
register("KRBK0004", handle)

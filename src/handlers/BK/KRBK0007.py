"""
KRBK0007 - 수협은행 (Suhyup-Bank 기업뱅킹 공지사항)

흐름:
  1. GET https://biz.suhyup-bank.com/ib20/mnu/CBM00334    → 목록 페이지
  2. div.boardWrap table tbody tr 파싱
     - td[0]=번호, td.alignLeft a (onclick="goDetail(NUM)") =제목+상세 키, td[2]=작성일
  3. #tokenForm input, #searchForm input 의 모든 hidden 값을 수집
  4. POST https://biz.suhyup-bank.com/ib20/mnu/CBM00334?ib20_wc=WID02426:WID02443
       form-encoded: searchForm + num + NO_REFRESH_TOKEN
     응답: 상세 페이지 HTML
  5. div.textArea (또는 td.cont) 에서 본문 추출
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://biz.suhyup-bank.com"
LIST_PATH = "/ib20/mnu/CBM00334"
DETAIL_PATH = "/ib20/mnu/CBM00334?ib20_wc=WID02426:WID02443"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_GO_DETAIL_RE = re.compile(r"goDetail\(\s*(\d+)\s*\)")


def _collect_form(soup: BeautifulSoup, selector: str) -> dict:
    out = {}
    for inp in soup.select(selector):
        name = inp.get("name")
        if name:
            out[name] = inp.get("value", "")
    return out


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    try:
        r = s.get(HOST + LIST_PATH, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[KRBK0007] 목록 GET 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    rows = soup.select("div.boardWrap table tbody tr")
    if not rows:
        logger.warning("[KRBK0007] 공지 목록 행 없음")
        return []

    token_form = _collect_form(soup, "#tokenForm input")
    search_form = _collect_form(soup, "#searchForm input")
    refresh_token = token_form.get("NO_REFRESH_TOKEN", "")

    s.headers.update({
        "Referer": HOST + LIST_PATH,
        "Content-Type": "application/x-www-form-urlencoded",
    })

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for tr in rows[:max_items]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        a = tds[1].find("a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue

        m = _GO_DETAIL_RE.search(a.get("onclick") or "")
        if not m:
            continue
        num = m.group(1)

        posted_date = tds[2].get_text(strip=True)
        posted_date = re.sub(r"\D", "", posted_date)

        # 상세 POST
        body = dict(search_form)
        body["num"] = num
        body["NO_REFRESH_TOKEN"] = refresh_token

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=body, timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            text_area = d_soup.select_one("div.textArea") or d_soup.select_one("div.boardView")
            if text_area:
                detail_html = str(text_area)
                body_text = text_area.get_text("\n", strip=True)
            else:
                detail_html = dr.text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRBK0007] 상세 POST 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0007] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0007", handle)

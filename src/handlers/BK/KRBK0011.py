"""
KRBK0011 - 농협은행 (NH 기업뱅킹 새로운 소식)

흐름 (GET ?saac=saac 트리거):
  1. GET https://mbiz.nonghyup.com/                                   → 쿠키
  2. GET https://mbiz.nonghyup.com/servlet/ICECP0500R.view?saac=saac  → 공지 목록 HTML
  3. #content table tbody tr 파싱:
       - td[0]: 번호
       - td.title a: 제목, data-tap="jsViewDetail('BBRD_SQNO')"
       - td[2]: 등록일 (예: '2026/05/11')
  4. GET /servlet/ICECP0501R.view?saac=saac&BbrdSqno=<BBRD_SQNO>      → 상세 페이지
  5. td.cont 안의 텍스트가 본문 (게시물 상세보기 내용 영역)
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

HOST = "https://mbiz.nonghyup.com"
LIST_PATH = "/servlet/ICECP0500R.view?saac=saac"
DETAIL_PATH = "/servlet/ICECP0501R.view"
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


_BBRD_RE = re.compile(r"jsViewDetail\(\s*['\"]?(\d+)['\"]?\s*\)")


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 워밍업
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0011] 워밍업 실패(무시): {e}")

    # 2) 목록
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[KRBK0011] 목록 GET 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    rows = soup.select("#content table tbody tr")
    if not rows:
        logger.warning("[KRBK0011] 공지 목록 행을 찾지 못함")
        return []

    max_items = getattr(site_config, "max_items", 15)
    s.headers["Referer"] = list_url
    results: List[HandlerResult] = []

    for tr in rows[:max_items]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        title_td = tds[1]
        a = title_td.find("a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue

        # data-tap 또는 onclick 에서 BBRD_SQNO 추출
        attr_str = a.get("data-tap") or a.get("onclick") or ""
        m = _BBRD_RE.search(attr_str)
        if not m:
            logger.debug(f"[KRBK0011] BBRD_SQNO 추출 실패: {attr_str!r} / {title}")
            continue
        bbrd_sqno = m.group(1)

        # 등록일 '2026/05/11' → '20260511'
        reg = tds[2].get_text(strip=True)
        posted_date = re.sub(r"\D", "", reg)

        # 상세 페이지
        detail_url = HOST + DETAIL_PATH
        try:
            dr = s.get(detail_url, params={"saac": "saac", "BbrdSqno": bbrd_sqno}, timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            # 본문 영역: td.cont (게시물 상세보기 내용 셀)
            cont = d_soup.select_one("td.cont")
            if cont is None:
                # 폴백 1: div#tongs2018, 그 외 view 류 셀렉터
                for sel in ("div#tongs2018", "div.cont", "div.boardView"):
                    cont = d_soup.select_one(sel)
                    if cont:
                        break
            if cont:
                detail_html = str(cont)
                body_text = cont.get_text("\n", strip=True)
            else:
                # 마지막 폴백: 페이지 전체
                detail_html = dr.text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRBK0011] 상세 GET 실패: {e}")
            detail_html = ""
            body_text = ""

        full_detail_url = f"{detail_url}?saac=saac&BbrdSqno={bbrd_sqno}"
        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=full_detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0011] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0011", handle)

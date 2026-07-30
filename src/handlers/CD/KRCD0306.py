"""
KRCD0306 - 신한카드 (Shinhan Card)

흐름:
  1) GET https://www.shinhancard.com (초기 쿠키)
  2) GET /cconts/html/customer/CRP57000/CRP57110/CRP57110.html (공지 목록 페이지, UTF-8)
     - ul#cmsListView > li 각 항목
       - a: 제목 + 상세 href (예: /cconts/html/customer/CRP57000/CRP57110/1238832_5631.html)
       - .sub_info2: 등록일 (YYYY.MM.DD.)
  3) 상세: GET {host}{href} (UTF-8)
     - div.borad_view 의 내용을 detail_html / body_text 로 사용
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

HOST = "https://www.shinhancard.com"
LIST_PATH = "/cconts/html/customer/CRP57000/CRP57110/CRP57110.html"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _normalize_date(s: str) -> str:
    """'2026.04.30.' / '2026-04-30' → '20260430'."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:8] if len(digits) >= 8 else digits


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 메인 GET → 쿠키
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRCD0306] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRCD0306] 목록 요청 실패: {e}")
        return []

    list_html = r.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(list_html, "lxml")

    ul = soup.select_one("ul#cmsListView")
    if ul is None:
        logger.warning("[KRCD0306] 목록 컨테이너(ul#cmsListView) 없음")
        return []

    items = ul.select("li")
    if not items:
        logger.info("[KRCD0306] 목록 항목 없음")
        return []

    results: List[HandlerResult] = []

    for li in items:
        if len(results) >= max_items:
            break

        a = li.select_one("a")
        if a is None:
            continue

        # 'NEW' 아이콘 등 제거
        for ico in a.select(".ico_new, .ico"):
            ico.decompose()
        title = a.get_text(strip=True)
        if not title:
            continue

        href = a.get("href") or ""
        if not href:
            continue
        detail_url = href if href.startswith("http") else HOST + href

        date_tag = li.select_one(".sub_info2")
        posted_date = _normalize_date(date_tag.get_text(strip=True) if date_tag else "")
        if len(posted_date) != 8:
            continue

        # 3) 상세 GET
        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=15)
            dr.raise_for_status()
            dt_html = dr.content.decode("utf-8", errors="replace")
            d_soup = BeautifulSoup(dt_html, "lxml")
            for tag in d_soup(["script", "style"]):
                tag.decompose()
            view = d_soup.select_one("div.borad_view")
            if view is not None:
                detail_html = str(view).strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_html
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRCD0306] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRCD0306] 핸들러 추출 {len(results)}건")
    return results


register("KRCD0306", handle)

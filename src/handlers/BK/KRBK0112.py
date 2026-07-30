"""
KRBK0112 - BNK저축은행 (공지사항)

흐름 (HTML, 원본 스크래퍼 그대로 포팅):
  1. GET /sub.do?code=03_news02  → 목록 HTML, #board_list 내 a 링크
  2. 각 링크 GET → 상세: 날짜 td.td_eng, 본문 td.td_con
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

HOST = "https://www.bnksb.com"
LIST_URL = HOST + "/sub.do?code=03_news02"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
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
    try:
        r = s.get(LIST_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0112] 목록 호출 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    board = soup.select_one("#board_list")
    if not board:
        logger.warning("[KRBK0112] #board_list 없음")
        return []

    seen: set[str] = set()
    results: List[HandlerResult] = []
    for a in board.select('a[href^="?"]'):
        href = (a.get("href") or "").replace("&amp;", "&")
        if not href or href in seen:
            continue
        seen.add(href)
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if not title:
            continue
        detail_url = HOST + "/sub.do" + href

        try:
            dr = s.get(detail_url, headers={"Referer": LIST_URL}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0112] 상세 실패: {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        date_node = dsoup.select_one("td.td_eng")
        posted_date = re.sub(r"[.\-/]", "", date_node.get_text(strip=True)).strip()[:8] if date_node else ""
        node = dsoup.select_one("td.td_con")
        if not node:
            continue
        for t in node.select("table.__se_tbl"):
            t.decompose()
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=detail_url,
            detail_html=str(node).strip(), body_text=node.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0112] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0112", handle)

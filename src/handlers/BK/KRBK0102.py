"""
KRBK0102 - 대신저축은행 (공지사항)

흐름 (HTML, 원본 스크래퍼 그대로 포팅):
  1. GET https://bank.daishin.com/sub.do?code=03_news02  → 목록 HTML
  2. table.table_board_basic 행 순회 (헤더: 번호/제목/첨부/작성자/등록일/조회)
  3. 각 행의 a href → GET 상세 → table.table_board_read td.td_con
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

HOST = "https://bank.daishin.com"
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
        logger.warning(f"[KRBK0102] 목록 호출 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    headers = [th.get_text(strip=True) for th in soup.select("table.table_board_basic thead th")]
    try:
        ti = headers.index("제목")
        di = headers.index("등록일")
    except ValueError:
        ti, di = 1, 4

    results: List[HandlerResult] = []
    for tr in soup.select("table.table_board_basic tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        a = tds[ti].select_one("a")
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title or not a:
            continue
        posted_date = re.sub(r"[.\-/]", "", tds[di].get_text(strip=True)).strip()
        href = (a.get("href") or "").replace("&amp;", "&")
        detail_url = HOST + "/sub.do" + href if href.startswith("?") else HOST + href

        try:
            dr = s.get(detail_url, headers={"Referer": LIST_URL}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0102] 상세 실패: {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        for sel in ("script", "#header", "#footer", "#rightMenu",
                    "#board_bottom", "#accessibilityFooter"):
            for n in dsoup.select(sel):
                n.decompose()
        node = dsoup.select_one("table.table_board_read tbody td.td_con")
        if not node:
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=detail_url,
            detail_html=str(node).strip(), body_text=node.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0102] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0102", handle)

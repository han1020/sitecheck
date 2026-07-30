"""
KRBK0105 - 웰컴저축은행 (공지사항, ib20 board)

흐름 (HTML, 원본 스크래퍼 그대로 포팅):
  1. GET /ib20/mnu/IBNBKINTC000  → 목록 HTML
  2. table.table_st1 행: 제목 a, 날짜, ibsGoViewPage('<article_id>')
  3. POST /ib20/mnu/IBNBKINTC000  (urlencoded inpData) → 상세
       상세 본문은 td.page_view textarea 안에 HTML-엔티티로 인코딩되어 있음
       → unescape 후 body 텍스트 추출, div.board_download_blue(첨부) 추가
"""
from __future__ import annotations

import html as _html
import logging
import re
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.welcomebank.co.kr"
LIST_URL = HOST + "/ib20/mnu/IBNBKINTC000"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_ID_RE = re.compile(r"ibsGoViewPage\('([^']+)'")
_DATE_RE = re.compile(r"(\d{4})[-.](\d{2})[-.](\d{2})")


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
    try:
        r = s.get(LIST_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0105] 목록 호출 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results: List[HandlerResult] = []

    for tr in soup.select("table.table_st1 tbody tr"):
        a = tr.select_one("td a")
        if not a:
            continue
        a_copy = BeautifulSoup(str(a), "lxml")
        for em in a_copy.select("em"):
            em.decompose()
        title = re.sub(r"\s+", " ", a_copy.get_text(" ", strip=True)).strip()
        if not title:
            continue

        row_html = str(tr)
        m = _ID_RE.search(row_html)
        if not m:
            continue
        article_id = m.group(1)

        dm = _DATE_RE.search(tr.get_text(" ", strip=True))
        posted_date = (dm.group(1) + dm.group(2) + dm.group(3)) if dm else ""

        inp = {
            "ibs.action": "", "ibs.target": "V", "ibs.article.id": article_id,
            "ibs.current.page": "", "b_page_id": "", "selectmenuid": "IBN000000000",
        }
        body = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in inp.items())
        try:
            dr = s.post(LIST_URL, data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Referer": LIST_URL}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0105] 상세 실패(id={article_id}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        ta = dsoup.select_one("td.page_view textarea")
        if ta:
            inner = _html.unescape(ta.decode_contents())
            inner_soup = BeautifulSoup(inner, "lxml")
            detail_html = (str(inner_soup.body) if inner_soup.body else inner).strip()
            body_text = inner_soup.get_text("\n", strip=True)
        else:
            node = dsoup.select_one("td.page_view") or dsoup
            detail_html = str(node).strip()
            body_text = node.get_text("\n", strip=True)

        attach = dsoup.select_one("div.board_download_blue")
        if attach:
            detail_html += str(attach)

        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=LIST_URL,
            detail_html=detail_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0105] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0105", handle)

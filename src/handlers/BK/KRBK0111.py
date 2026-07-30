"""
KRBK0111 - OSB저축은행 (공지사항, ib20 board)

흐름 (HTML, 원본 스크래퍼 그대로 포팅):
  1. GET /ib20/mnu/HOM00034  → 목록 HTML
  2. div.table3 table 행: 번호/제목/작성일, 제목 a class="act-view-<P_IDX>"
  3. POST /ib20/mnu/HOM00034  body: ib20_wc=WID00347%3AWID00348&P_IDX=<P_IDX>
       → div.table1 tbody div.viewbox
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

HOST = "https://www.osb.co.kr"
LIST_URL = HOST + "/ib20/mnu/HOM00034"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
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
    try:
        r = s.get(LIST_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0111] 목록 호출 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    headers = [th.get_text(strip=True) for th in soup.select("div.table3 table thead th")]
    try:
        ti = headers.index("제목")
        di = headers.index("작성일")
    except ValueError:
        ti, di = 1, 2

    results: List[HandlerResult] = []
    for tr in soup.select("div.table3 table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-/]", "", tds[di].get_text(strip=True)).strip()
        a = tds[ti].select_one("a")
        p_idx = ""
        if a:
            for cls in (a.get("class") or []):
                if cls.startswith("act-view-"):
                    p_idx = cls[len("act-view-"):]
                    break
        if not p_idx:
            continue

        body = f"ib20_wc=WID00347%3AWID00348&P_IDX={p_idx}"
        try:
            dr = s.post(LIST_URL, data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Referer": LIST_URL}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0111] 상세 실패(P_IDX={p_idx}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        for n in dsoup.select("script"):
            n.decompose()
        node = dsoup.select_one("div.table1 tbody div.viewbox") or dsoup.select_one("div.viewbox")
        if not node:
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=LIST_URL,
            detail_html=str(node).strip(), body_text=node.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0111] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0111", handle)

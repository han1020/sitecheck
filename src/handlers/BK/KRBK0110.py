"""
KRBK0110 - NH저축은행 (공지사항)

흐름 (HTML, 원본 스크래퍼 그대로 포팅):
  1. GET /notice/list.do  → 목록 HTML + totalCnt(hidden)
  2. div.tb_type_bbs table 행: 번호/제목/등록일/조회수, a onclick=funBrdRead('SNO')
  3. POST /notice/view.do (urlencoded) → div.tb_type_row table
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

HOST = "https://www.nhsavingsbank.co.kr"
LIST_URL = HOST + "/notice/list.do"
VIEW_URL = HOST + "/notice/view.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_SNO_RE = re.compile(r"funBrdRead\('([^']+)'\)")
_TOTAL_RE = re.compile(r'name="totalCnt"[^>]*value="(\d+)"|value="(\d+)"[^>]*name="totalCnt"')


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
        logger.warning(f"[KRBK0110] 목록 호출 실패: {e}")
        return []

    m = _TOTAL_RE.search(r.text)
    total_cnt = (m.group(1) or m.group(2)) if m else "0"

    soup = BeautifulSoup(r.text, "lxml")
    headers = [th.get_text(strip=True) for th in soup.select("div.tb_type_bbs table thead th")]
    try:
        ti = headers.index("제목")
        di = headers.index("등록일")
    except ValueError:
        ti, di = 1, 2

    results: List[HandlerResult] = []
    for tr in soup.select("div.tb_type_bbs table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-/]", "", tds[di].get_text(strip=True)).strip()
        a = tds[ti].select_one("a")
        m2 = _SNO_RE.search(a.get("onclick", "") or a.get("href", "")) if a else None
        if not m2:
            continue
        brd_sno = m2.group(1)

        body = (
            f"pageIndex=1&totalCnt={total_cnt}&currentRowPerPage=11"
            f"&brd_sno={brd_sno}&srch_titl=&ktb_agent="
        )
        try:
            dr = s.post(VIEW_URL, data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Referer": LIST_URL}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0110] 상세 실패(sno={brd_sno}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        for n in dsoup.select("script"):
            n.decompose()
        node = dsoup.select_one("div.tb_type_row table")
        if not node:
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=VIEW_URL,
            detail_html=str(node).strip(), body_text=node.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0110] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0110", handle)

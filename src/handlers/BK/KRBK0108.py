"""
KRBK0108 - 하나저축은행 (공지사항)

흐름 (HTML, authorization 응답헤더 릴레이, 원본 스크래퍼 그대로 포팅):
  1. GET /                              → 응답헤더 authorization 캡처
  2. GET /CUS/CUS0101?Authorization=<auth>  → 목록 HTML + authorization 재캡처
  3. 목록: div.container_inner table 행 (번호=th, 제목/작성자/등록일/조회수=td)
       제목 a onclick=goBoardInfo(<seq>), totalCount(hidden)
  4. POST /CUS/CUS010101 (urlencoded, Authorization 포함) → div.detail_text
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

HOST = "https://www.hanasavings.com"
LIST_PATH = "/CUS/CUS0101"
DETAIL_URL = HOST + "/CUS/CUS010101"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_SEQ_RE = re.compile(r"goBoardInfo\(\s*'?([0-9]+)'?\s*\)")
_TOTAL_RE = re.compile(r'name="totalCount"[^>]*value="(\d+)"|value="(\d+)"[^>]*name="totalCount"')


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    try:
        r0 = s.get(HOST + "/", timeout=20)
        auth = r0.headers.get("authorization") or ""
        list_url = f"{HOST}{LIST_PATH}?Authorization={auth}"
        r = s.get(list_url, headers={"Referer": HOST}, timeout=20)
        r.raise_for_status()
        auth = r.headers.get("authorization") or auth
    except Exception as e:
        logger.warning(f"[KRBK0108] 목록 호출 실패: {e}")
        return []

    m = _TOTAL_RE.search(r.text)
    total_count = (m.group(1) or m.group(2)) if m else "0"

    soup = BeautifulSoup(r.text, "lxml")
    headers = [th.get_text(strip=True) for th in soup.select("div.container_inner table thead th")]
    try:
        # 번호는 th 라서 td 인덱스는 헤더 인덱스 - 1
        ti = headers.index("제목") - 1
        di = headers.index("등록일") - 1
    except ValueError:
        ti, di = 0, 2

    results: List[HandlerResult] = []
    for tr in soup.select("div.container_inner table tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(ti, di):
            continue
        title = re.sub(r"\s+", " ", tds[ti].get_text(" ", strip=True)).strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-/]", "", tds[di].get_text(strip=True)).strip()[:8]
        a = tr.select_one("a")
        sm = _SEQ_RE.search(a.get("onclick", "") if a else "")
        if not sm:
            continue
        seq = sm.group(1)

        body = (
            f"pageNo=1&id={seq}&boardId=3&totalCount={total_count}"
            "&searchKind=&searchWord=&pageId=CUS0101&pagePerRow=10"
            "&forwardPageId=CUS010101&scrId=CUS0101&popupView=N"
            f"&Authorization={auth}"
        )
        try:
            dr = s.post(DETAIL_URL, data=body,
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "Referer": list_url}, timeout=20)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0108] 상세 실패(id={seq}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        for n in dsoup.select("script"):
            n.decompose()
        node = dsoup.select_one("div.container_inner div.detail_text") or dsoup.select_one("div.detail_text")
        if not node:
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=DETAIL_URL,
            detail_html=str(node).strip(), body_text=node.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0108] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0108", handle)

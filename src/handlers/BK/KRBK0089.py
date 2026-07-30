"""
KRBK0089 - K뱅크 (기업뱅킹 공지사항)

흐름 (ib20 프레임워크, form-token POST, 원본 스크래퍼 그대로 포팅):
  1. GET  https://biz.kbanknow.com/                           → 쿠키
  2. GET  /ib20/mnu/CBKCSC020100                              → 목록 HTML + pageTokenKey
  3. HTML 에서 `var pageTokenKey = "..."` 추출
  4. ul.notice_list li 순회
       a 첫 자식 = 글번호, p.message(strong 제거) = 제목, .date(span 제거) = 등록일
  5. POST /ib20/mnu/CBKCSC020100?...&pbntId={no}  (form, REQUEST_TOKEN_KEY 포함)
       → 상세 HTML (div.board_content)
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

HOST = "https://biz.kbanknow.com"
LIST_PATH = "/ib20/mnu/CBKCSC020100"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_TOKEN_RE = re.compile(r'var pageTokenKey = "([^"]+)"')


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    try:
        s.get(HOST + "/", timeout=20)
        r = s.get(HOST + LIST_PATH, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0089] 목록 페이지 호출 실패: {e}")
        return []

    m = _TOKEN_RE.search(r.text)
    if not m:
        logger.warning("[KRBK0089] pageTokenKey 추출 실패")
        return []
    token = m.group(1)

    soup = BeautifulSoup(r.text, "lxml")
    results: List[HandlerResult] = []

    for li in soup.select("ul.notice_list li"):
        a = li.select_one("a")
        if not a:
            continue
        first_child = a.find(recursive=False)
        notice_no = (first_child.get_text(strip=True) if first_child else "").strip()
        if not notice_no:
            continue

        msg = li.select_one("p.message")
        if not msg:
            continue
        for strong in msg.find_all("strong"):
            strong.decompose()
        title = re.sub(r"\s+", " ", msg.get_text(" ", strip=True)).strip()
        if not title:
            continue

        date_node = li.select_one(".date")
        if date_node:
            for sp in date_node.find_all("span"):
                sp.decompose()
            posted_date = re.sub(r"[.\-]", "", date_node.get_text(strip=True)).strip()
        else:
            posted_date = ""

        body = (
            "selectmenuid=CBKCSC020100%3Fib20_wc%3DCBKCSC0201000000V"
            "%3ACBKCSC0201000010V%26pbntId%3D" + notice_no
            + "&language=&REQUEST_TOKEN_KEY=" + token
            + "&b_page_id=&org_menu_id=CBKCSC020100"
            "&org_menu_id_history=CBKCSC020100"
        )
        dt_link = (
            f"{HOST}{LIST_PATH}?ib20_wc=CBKCSC0201000000V:"
            f"CBKCSC0201000010V&pbntId={notice_no}"
        )

        try:
            dr = s.post(
                dt_link, data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0089] 상세 조회 실패(pbntId={notice_no}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        node = dsoup.select_one("div.board_content")
        if not node:
            continue
        detail_html = str(node).strip()
        body_text = node.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=dt_link,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0089] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0089", handle)

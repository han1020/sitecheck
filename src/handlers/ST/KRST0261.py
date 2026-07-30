"""
KRST0261 - 교보증권 (Kyobo Securities)

흐름:
  1. GET https://www.iprovest.com (초기 쿠키)
  2. GET https://www.iprovest.com/weblogic/MNBoardServlet?scr_id=3&mode=konglist&webuseyn=Y&pubyn=Y
     - 응답 인코딩: cp949 (euc-kr)
     - table.pb_Gtable thead/tbody 파싱
     - thead: ['번호', '제목', '작성일', '조회수'] (사이트에 따라 '일자' 일 수 있음)
     - 제목 칸의 <a href>에서 절대 URL 구성 (호스트 prefix 부여)
  3. 상세: GET {hostURL}{href}
     - cp949 디코딩
     - div#print_content1 영역을 detail_html / body_text로 사용
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.iprovest.com"
LIST_PATH = "/weblogic/MNBoardServlet?scr_id=3&mode=konglist&webuseyn=Y&pubyn=Y"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

# 작성일/일자 등 날짜 컬럼 후보
_DATE_COL_CANDIDATES = ("작성일", "일자", "등록일")


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등 → 'YYYYMMDD' 8자리."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:8]


def handle(site_config) -> List[HandlerResult]:
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

    # 1) 초기 쿠키
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0261] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET
    try:
        r = s.get(HOST + LIST_PATH, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0261] 목록 요청 실패: {e}")
        return []

    try:
        list_html = r.content.decode("cp949", errors="replace")
    except Exception:
        list_html = r.text

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("table.pb_Gtable")
    if table is None:
        logger.warning("[KRST0261] 목록 테이블(table.pb_Gtable) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0261] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_title = col_idx("제목")
    idx_date = -1
    for cand in _DATE_COL_CANDIDATES:
        i = col_idx(cand)
        if i >= 0:
            idx_date = i
            break

    if idx_title < 0:
        logger.warning(f"[KRST0261] 제목 컬럼 없음 (thead={thead_names})")
        return []

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0261] tbody tr 없음")
        return []

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds or len(tds) <= idx_title:
            continue

        title_td = tds[idx_title]
        a = title_td.find("a")
        if a is None:
            continue

        title = a.get_text(strip=True) or title_td.get_text(strip=True)
        if not title:
            continue

        href = a.get("href") or ""
        if not href:
            continue
        detail_url = urljoin(HOST + "/", href)

        posted_date = ""
        if 0 <= idx_date < len(tds):
            posted_date = _normalize_date(tds[idx_date].get_text(strip=True))

        # 3) 상세 GET
        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            try:
                dt_text = dr.content.decode("cp949", errors="replace")
            except Exception:
                dt_text = dr.text

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 불필요 요소 제거
            for tag in d_soup(["script", "meta", "title", "iframe"]):
                tag.decompose()
            for tag in d_soup.select("#hwpEditorBoardContent"):
                tag.decompose()
            for tag in d_soup.select("div.pb_popFooter"):
                tag.decompose()

            content = d_soup.select_one("div#print_content1")
            if content is not None:
                detail_html = content.decode_contents().strip()
                body_text = content.get_text("\n", strip=True)
            else:
                # fallback
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0261] 상세 요청 실패({title[:30]}): {e}")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0261] 핸들러 추출 {len(results)}건")
    return results


register("KRST0261", handle)

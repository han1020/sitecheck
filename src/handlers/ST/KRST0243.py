"""
KRST0243 - 한국투자증권 (Korea Investment & Securities)

흐름:
  1. GET https://securities.koreainvestment.com (초기 쿠키)
  2. GET /main/customer/notice/Notice.jsp?cmd=TF04ga000001&...&fromDate={today}&toDate={today}&...
       Referer: https://securities.koreainvestment.com/main/customer/notice/Notice.jsp
     목록 HTML 파싱 (div.tableDefault table)
       - thead th 로 컬럼 순서 결정 ('번호', '구분', '제목', '첨부파일', '등록일')
       - 제목 칸의 <a> onclick/href에서 doView('NUM',...) 형식으로 num 추출
  3. 상세: POST /main/customer/notice/Notice.jsp?cmd=TF04ga000002&num={num}&...
       응답 HTML 의 #ifrmContent 내용을 detail_html / body_text 로 사용
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://securities.koreainvestment.com"
NOTICE_BASE = "/main/customer/notice/Notice.jsp"
REFERER = HOST + NOTICE_BASE

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36"
)

_DOVIEW_RE = re.compile(r"doView\('([^']+)'")


def _extract_num(a_tag) -> str:
    """<a> 의 href / onclick 에서 doView('NUM', ...) 의 NUM 추출."""
    if a_tag is None:
        return ""
    for attr in ("href", "onclick"):
        val = a_tag.get(attr) or ""
        m = _DOVIEW_RE.search(val)
        if m:
            return m.group(1).strip()
    return ""


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' / '2025/05/29' 등에서 숫자만 추출."""
    return re.sub(r"\D", "", s or "")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
        "Pragma": "no-cache",
    })

    # 1) 초기 쿠키 획득
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0243] 초기 GET 실패(무시): {e}")

    # 이후 요청에는 Referer 부여
    s.headers.update({"Referer": REFERER})

    today = date.today().strftime("%Y.%m.%d")

    list_url = (
        f"{HOST}{NOTICE_BASE}"
        f"?cmd=TF04ga000001&num=&currentPage=1&flag=&focusYN=btn_focus"
        f"&userRowsPerPage=50&searchDate=all"
        f"&fromDate={today}&toDate={today}"
        f"&searchColumn=all&searchValue="
    )

    # 2) 목록 GET
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0243] 목록 요청 실패: {e}")
        return []

    # UTF-8 우선
    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("div.tableDefault table")
    if not table:
        logger.warning("[KRST0243] 목록 테이블(div.tableDefault table) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0243] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_no = col_idx("번호")
    idx_cat = col_idx("구분")
    idx_title = col_idx("제목")
    idx_date = col_idx("등록일")

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0243] tbody tr 없음 (오늘 공지 없음)")
        return []

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        def cell_text(i: int) -> str:
            if i < 0 or i >= len(tds):
                return ""
            return tds[i].get_text(strip=True)

        notice_no = cell_text(idx_no)  # noqa: F841 (기록용)
        category = cell_text(idx_cat)  # noqa: F841
        title = ""
        num = ""
        if 0 <= idx_title < len(tds):
            a = tds[idx_title].find("a")
            if a is not None:
                title = a.get_text(strip=True)
                num = _extract_num(a)
            if not title:
                title = tds[idx_title].get_text(strip=True)

        posted_date = _normalize_date(cell_text(idx_date))

        if not title or not num:
            continue

        detail_url = (
            f"{HOST}{NOTICE_BASE}"
            f"?cmd=TF04ga000002&num={num}&currentPage=1&flag=&focusYN="
            f"&userRowsPerPage=50&searchDate=all"
            f"&fromDate={today}&toDate={today}"
            f"&searchColumn=all&searchValue="
        )

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(detail_url, timeout=20)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")
            content = d_soup.select_one("#ifrmContent")
            if content is not None:
                detail_html = content.decode_contents()
                body_text = content.get_text("\n", strip=True)
            else:
                # fallback
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0243] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0243] 핸들러 추출 {len(results)}건")
    return results


register("KRST0243", handle)

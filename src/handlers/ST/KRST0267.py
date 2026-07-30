"""
KRST0267 - 대신증권 (Daishin Securities)

흐름:
  1) GET https://money2.daishin.com/e5/mboard/ptype_basic/basic_001/DW_Basic_List.aspx
        ?boardseq=114&p=1385&v=2247&m=1108
     → table.tableDefault thead th 로 컬럼 순서 결정 ('작성일', '제목', '첨부파일', '조회')
       tbody tr 의 제목/작성일을 추출 (작성일이 '공지' 인 row 는 skip)
  2) 상세: 제목 셀 <a href> 는 './DW_Basic_Read.aspx?...' 형태 → 호스트 + 'e5/mboard/ptype_basic/basic_001' 결합
        Referer 는 목록 URL
        응답 HTML 에서 div.viewBox 내용을 detail_html / body_text 로 사용
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

HOST = "https://money2.daishin.com"
LIST_PATH = "/e5/mboard/ptype_basic/basic_001/DW_Basic_List.aspx?boardseq=114&p=1385&v=2247&m=1108"
LIST_URL = HOST + LIST_PATH
DETAIL_BASE = HOST + "/e5/mboard/ptype_basic/basic_001"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)


def _normalize_date(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
        "Connection": "keep-alive",
    })

    # 1) 목록 GET
    try:
        r = s.get(LIST_URL, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0267] 목록 요청 실패: {e}")
        return []

    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("table.tableDefault")
    if not table:
        logger.warning("[KRST0267] 목록 테이블(table.tableDefault) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0267] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_title = col_idx("제목")
    idx_date = col_idx("작성일")

    if idx_title < 0 or idx_date < 0:
        logger.warning(f"[KRST0267] 필수 컬럼 누락: thead={thead_names}")
        return []

    rows = table.select("tbody tr")
    if not rows:
        logger.warning("[KRST0267] tbody tr 없음")
        return []

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        if idx_title >= len(tds) or idx_date >= len(tds):
            continue

        title_cell = tds[idx_title]
        date_cell = tds[idx_date]

        raw_date = date_cell.get_text(strip=True)
        # '공지' 인 row 는 skip
        if raw_date == "공지":
            continue

        posted_date = _normalize_date(raw_date)

        a_tag = title_cell.find("a")
        title = ""
        href = ""
        if a_tag is not None:
            title = re.sub(r"\s+", " ", a_tag.get_text()).strip()
            href = a_tag.get("href") or ""
        if not title:
            title = re.sub(r"\s+", " ", title_cell.get_text()).strip()

        if not title or not href:
            continue

        # JS: href 가 '.' 으로 시작하면 한 글자 제거 후 DETAIL_BASE 와 합침
        if href.startswith("."):
            href = href[1:]
        if href.startswith("/"):
            detail_url = DETAIL_BASE + href
        else:
            # 상대 경로
            detail_url = urljoin(DETAIL_BASE + "/", href)

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(
                detail_url,
                headers={"Referer": LIST_URL},
                timeout=20,
            )
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 상세 페이지 마크업이 깨져 있어 lxml 이 div#contents 를 div#header 안으로
            # 중첩시킨다. viewBox 를 먼저 잡아둬야 #header 제거가 본문까지 지우지 않는다.
            view = d_soup.select_one("div.viewBox")

            # 불필요 요소 제거
            for sel in [
                "script", "#skipNavi", "#header", "#aside", "#footer",
                "div.aspNetHidden", "div.layerPopup", "div.dataArea",
                "div.fileArea", "div.btnArea", "div.btnSnsArea",
            ]:
                for t in (view or d_soup).select(sel):
                    t.decompose()

            if view is not None:
                detail_html = view.decode_contents().strip()
                body_text = re.sub(r"\s+", " ", view.get_text(" ", strip=True)).strip()
            else:
                detail_html = dt_text
                body_text = re.sub(r"\s+", " ", d_soup.get_text(" ", strip=True)).strip()
        except Exception as e:
            logger.debug(f"[KRST0267] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0267] 핸들러 추출 {len(results)}건")
    return results


register("KRST0267", handle)

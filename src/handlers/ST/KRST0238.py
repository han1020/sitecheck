"""
KRST0238 - 미래에셋증권 (Mirae Asset Securities)

흐름:
  1. GET https://securities.miraeasset.com/main.do                       → 쿠키
  2. GET /bbs/board/message/list.do?categoryId=66&selectedId=1000000202&selectedId=1000000203&selectedId=1000003101
     (Referer: /main.do, 응답 EUC-KR)
     - table.bbs_linetype2 tbody tr 에서 제목/작성일 파싱
     - <a> 의 href="javascript:view('messageId','messageNumber')"
       에서 messageId / messageNumber 추출
     - id="viewForm" 내 hidden 필드(vf_*) 들을 모두 수집해 상세 URL 구성
  3. GET /bbs/board/message/view.do?... (응답 EUC-KR)
     - <td class="bbs_detail_view"> HTML / 텍스트 추출
"""
from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://securities.miraeasset.com"
MAIN_PATH = "/main.do"
LIST_PATH = (
    "/bbs/board/message/list.do"
    "?categoryId=66"
    "&selectedId=1000000202"
    "&selectedId=1000000203"
    "&selectedId=1000003101"
)
DETAIL_PATH = "/bbs/board/message/view.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_VIEW_PARAM_RE = re.compile(r"view\(\s*'([^']*)'\s*,\s*'([^']*)'")
_DATE_NORMALIZE_RE = re.compile(r"[.\-/]")
_WS_RE = re.compile(r"\s+")


def _get_vf_value(viewform_div, field_id: str) -> str:
    """viewForm div 안에서 id=field_id 인 input의 value 반환."""
    if viewform_div is None:
        return ""
    inp = viewform_div.find("input", {"id": field_id})
    if inp is None:
        return ""
    return inp.get("value") or ""


def _get_vf_values(viewform_div, field_id: str) -> List[str]:
    """동일 id가 여러 개인 경우 모두 수집(예: vf_selectedId)."""
    if viewform_div is None:
        return []
    inputs = viewform_div.find_all("input", {"id": field_id})
    return [(i.get("value") or "") for i in inputs]


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko,en;q=0.9,en-US;q=0.8",
        "User-Agent": USER_AGENT,
        "Connection": "keep-alive",
    })

    # 1) 메인 진입 → 쿠키
    try:
        s.get(HOST + MAIN_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0238] 메인 진입 실패(무시): {e}")

    s.headers.update({"Referer": HOST + MAIN_PATH})

    # 2) 목록 페이지
    try:
        r = s.get(HOST + LIST_PATH, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0238] 목록 호출 실패: {e}")
        return []

    list_html = r.content.decode("euc-kr", errors="replace")
    soup = BeautifulSoup(list_html, "lxml")

    # script 제거 (참조 JS와 동일)
    for sc in soup.find_all("script"):
        sc.decompose()

    # viewForm hidden 필드 수집
    viewform_div = soup.find(id="viewForm")
    message_category_id = _get_vf_value(viewform_div, "vf_messageCategoryId")
    start_id = _get_vf_value(viewform_div, "vf_startId")
    start_page = _get_vf_value(viewform_div, "vf_startPage")
    cur_page = _get_vf_value(viewform_div, "vf_curPage")
    search_type = _get_vf_value(viewform_div, "vf_searchType")
    search_text = _get_vf_value(viewform_div, "vf_searchText")
    search_start_year = _get_vf_value(viewform_div, "vf_searchStartYear")
    search_start_month = _get_vf_value(viewform_div, "vf_searchStartMonth")
    search_start_day = _get_vf_value(viewform_div, "vf_searchStartDay")
    search_end_year = _get_vf_value(viewform_div, "vf_searchEndYear")
    search_end_month = _get_vf_value(viewform_div, "vf_searchEndMonth")
    search_end_day = _get_vf_value(viewform_div, "vf_searchEndDay")
    last_page_flag = _get_vf_value(viewform_div, "vf_lastPageFlag")
    vf_header_title = _get_vf_value(viewform_div, "vf_headerTitle")
    category_id = _get_vf_value(viewform_div, "vf_categoryId")
    selected_ids = _get_vf_values(viewform_div, "vf_selectedId")
    # 기대값 3개, 부족하면 빈 문자열로 패딩
    while len(selected_ids) < 3:
        selected_ids.append("")

    # 목록 테이블
    table = soup.find("table", class_="bbs_linetype2")
    if table is None:
        logger.warning("[KRST0238] table.bbs_linetype2 미발견")
        return []

    thead_ths = table.select("thead th")
    thead_names = [th.get_text(strip=True) for th in thead_ths]
    try:
        idx_title = thead_names.index("제목")
        idx_date = thead_names.index("작성일")
    except ValueError:
        logger.warning(f"[KRST0238] thead 컬럼 미발견: {thead_names}")
        return []

    tbody = table.find("tbody")
    if tbody is None:
        logger.warning("[KRST0238] tbody 미발견")
        return []

    rows = tbody.find_all("tr", recursive=False)
    results: List[HandlerResult] = []

    for row in rows:
        if len(results) >= max_items:
            break

        tds = row.find_all("td", recursive=False)
        # JS: th 가 있는 경우 td 인덱스 보정 (tdIdx = 1 → th 없을 때 0)
        td_idx_shift = 0 if row.find("th") is None else 1

        try:
            title_td = tds[idx_title - td_idx_shift] if idx_title - td_idx_shift >= 0 else tds[idx_title]
            date_td = tds[idx_date - td_idx_shift] if idx_date - td_idx_shift >= 0 else tds[idx_date]
        except IndexError:
            continue

        # 제목
        a_tag = title_td.find("a")
        if a_tag is None:
            continue
        raw_title = title_td.get_text(" ", strip=True)
        title = _WS_RE.sub(" ", raw_title).strip()
        if not title:
            continue

        # href 에서 view(...) 인자 추출
        href = a_tag.get("href") or ""
        # onclick 에 있을 수도 있음
        onclick = a_tag.get("onclick") or ""
        view_src = href if "view(" in href else onclick

        m = _VIEW_PARAM_RE.search(view_src)
        if not m:
            logger.debug(f"[KRST0238] view() 파싱 실패: title={title[:30]} href={view_src[:80]}")
            continue
        # JS: messageId = viewParam.grap("'","'")     → 첫 번째 따옴표 인자
        #     resNotiNo  = viewParam.grap("'","'", 2) → 두 번째 따옴표 인자
        message_id = m.group(1)
        res_noti_no = m.group(2)

        # 작성일 → YYYYMMDD
        raw_date = date_td.get_text(" ", strip=True)
        posted_date = _DATE_NORMALIZE_RE.sub("", raw_date).strip()

        # 상세 URL 구성 (selectedId 3개)
        detail_url = (
            f"{HOST}{DETAIL_PATH}"
            f"?messageId={message_id}"
            f"&messageNumber={res_noti_no}"
            f"&messageCategoryId={message_category_id}"
            f"&startId={quote(start_id, safe='')}"
            f"&startPage={start_page}"
            f"&curPage={cur_page}"
            f"&searchType={search_type}"
            f"&searchText={search_text}"
            f"&searchStartYear={search_start_year}"
            f"&searchStartMonth={search_start_month}"
            f"&searchStartDay={search_start_day}"
            f"&searchEndYear={search_end_year}"
            f"&searchEndMonth={search_end_month}"
            f"&searchEndDay={search_end_day}"
            f"&lastPageFlag={last_page_flag}"
            f"&vf_headerTitle={vf_header_title}"
            f"&categoryId={category_id}"
            f"&selectedId={selected_ids[0]}"
            f"&selectedId={selected_ids[1]}"
            f"&selectedId={selected_ids[2]}"
        )

        # 상세 페이지
        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=15)
            dr.raise_for_status()
            d_content = dr.content.decode("euc-kr", errors="replace")
            d_soup = BeautifulSoup(d_content, "lxml")
            for tag in d_soup.find_all(["script"]):
                tag.decompose()
            for sel in ["#headerWrap", "#footerWrap"]:
                el = d_soup.select_one(sel)
                if el:
                    el.decompose()
            for el in d_soup.select("div.btn-right"):
                el.decompose()
            for el in d_soup.select("ul.bbs_detail_list"):
                el.decompose()

            detail_td = d_soup.select_one("td.bbs_detail_view")
            if detail_td is not None:
                detail_html = detail_td.decode_contents().strip()
                # 텍스트
                body_text = detail_td.get_text("\n", strip=True)
                # 이미지 alt 도 본문 끝에 추가 (참조 JS와 유사)
                alts = [
                    (img.get("alt") or "").strip()
                    for img in detail_td.find_all("img")
                    if (img.get("alt") or "").strip()
                ]
                if alts:
                    body_text = (body_text + "\n" + "\n".join(alts)).strip()
            else:
                # fallback: 페이지 전체
                detail_html = d_content
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0238] 상세 호출 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0238] 핸들러 추출 {len(results)}건")
    return results


register("KRST0238", handle)

"""
KRST1247 - 모바일증권 나무 (NH나무)

흐름:
  1. GET https://www.mynamuh.com (초기 쿠키)
     - 'WMONID=' 빈값 쿠키를 미리 세팅해야 NH투자증권 보안 정책 차단을 회피
  2. POST https://www.mynamuh.com/tx/wooriwmBoard/boardList.action
       ?wm_menu_code=1495,1550,1551&iPg=1&iPageSize=20
       &sType_Cd=3000000000&sBoard_Id=17&keyfield=whole&research=
     응답 EUC-KR HTML 의 table#dataTable 파싱
       - thead th 로 컬럼 순서 결정 ('번호', '구분', '제목', '작성일', '조회수')
       - 제목 칸의 <a> onclick="viewUp('a','b','c','d','e','f')" 6개 파라미터 추출
  3. 상세: GET /tx/wooriwmBoard/boardView.action?...
       응답 EUC-KR HTML 의 div.viewContents 를 detail_html / body_text 로 사용
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

HOST = "https://www.mynamuh.com"
# 사이트 측 봇 탐지(/tx/ 라우트 POST 차단)를 우회하기 위해
# 일반 라우트의 GET 으로 접근한다.
LIST_PATH = (
    "/wooriwmBoard/boardList.action"
    "?wm_menu_code=1495,1550,1551&iPg=1&iPageSize=20"
    "&sType_Cd=3000000000&sBoard_Id=17&keyfield=whole&research="
)
DETAIL_PATH = "/wooriwmBoard/boardView.action"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_VIEWUP_RE = re.compile(r"viewUp\(([^)]*)\)")


def _extract_viewup_params(onclick: str) -> List[str]:
    """onclick="viewUp('a','b','c','d','e','f')" → ['a','b','c','d','e','f']"""
    if not onclick:
        return []
    m = _VIEWUP_RE.search(onclick)
    if not m:
        return []
    inner = m.group(1).strip()
    # 공백/줄바꿈 제거 후 양끝 따옴표 제거
    inner = re.sub(r"\s+", "", inner)
    if len(inner) >= 2 and inner[0] in ("'", '"') and inner[-1] in ("'", '"'):
        inner = inner[1:-1]
    # split by ',' 또는 "','"
    parts = re.split(r"['\"]\s*,\s*['\"]", inner)
    return [p.strip() for p in parts]


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    })

    # 1) 메인 GET 으로 일반 세션 흐름 흉내
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRST1247] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET (POST 는 /tx/ 라우트에서 차단됨)
    try:
        r = s.get(HOST + LIST_PATH, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST1247] 목록 요청 실패: {e}")
        return []

    list_html = r.content.decode("euc-kr", errors="replace")
    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("table#dataTable")
    if not table:
        logger.warning("[KRST1247] 목록 테이블(table#dataTable) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST1247] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_no = col_idx("번호")
    idx_title = col_idx("제목")
    idx_date = col_idx("작성일")

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST1247] tbody tr 없음")
        return []

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

        # 제목 (공백 정리)
        title = ""
        a_tag = None
        if 0 <= idx_title < len(tds):
            title_cell = tds[idx_title]
            title_raw = title_cell.get_text(" ", strip=True)
            title = re.sub(r"\s+", " ", title_raw).strip()
            a_tag = title_cell.find("a")
        if not title:
            continue

        # 작성일 → 'YYYYMMDD'
        posted_date_raw = cell_text(idx_date)
        posted_date = re.sub(r"[-.]", "", posted_date_raw).strip()

        # onclick 에서 viewUp 파라미터 추출
        onclick_val = ""
        if a_tag is not None:
            onclick_val = a_tag.get("onclick") or ""
        if not onclick_val:
            # 행 전체에서 찾기 (fallback)
            any_a = tr.find("a", attrs={"onclick": True})
            if any_a is not None:
                onclick_val = any_a.get("onclick") or ""

        params = _extract_viewup_params(onclick_val)
        if len(params) < 6:
            logger.debug(f"[KRST1247] viewUp 파라미터 추출 실패: {onclick_val!r}")
            continue

        query_string = (
            "wm_menu_code=1495,1550,1551"
            f"&sum={params[0]}"
            f"&board_id={params[2]}"
            f"&type_cd={params[1]}"
            "&viewUp=viewUp"
            f"&main_no={params[3]}"
            f"&sub_no={params[4]}"
            f"&answer_lvl={params[5]}"
            "&check=view"
            "&iPg=1&iPageSize=20"
            "&sType_Cd=3000000000&sBoard_Id=17&keyfield=whole"
        )
        detail_url = f"{HOST}{DETAIL_PATH}?{query_string}"

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            dt_text = dr.content.decode("euc-kr", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 불필요 태그 제거
            for sel in [
                "script",
                "#logoutCheckPage",
                "div.topContentsV",
                "div.btmList",
                "div.btnAreaV",
                "div.procssV",
                "#lnbAreaV",
                "#headerV",
                "#footerV",
                "#detailTable",
            ]:
                for tag in d_soup.select(sel):
                    tag.decompose()

            view = d_soup.select_one("div.viewContents")
            if view is not None:
                detail_html = view.decode_contents().strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST1247] 상세 요청 실패({title[:30]}): {e}")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST1247] 핸들러 추출 {len(results)}건")
    return results


register("KRST1247", handle)

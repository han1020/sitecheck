"""
KRST0247 - NH투자증권 (NH Investment & Securities)

흐름:
  1) GET https://www.nhqv.com (초기 쿠키)
  2) GET /wooriwmBoard/boardList.action?wm_menu_code=72,78,523
        &iPg=1&iPageSize=20&sType_Cd=0000000002&sBoard_Id=1
        &keyfield=whole&research=
     응답 HTML(EUC-KR) → table#dataTable thead/tbody 파싱
       - thead th 텍스트로 컬럼 인덱스 결정 ('번호', '제목', '작성일')
       - tbody tr 의 <a onclick="viewUp('sum','type','board','main','sub','answer')">
         형식에서 6개 파라미터 추출
  3) 상세: GET /wooriwmBoard/boardView.action?wm_menu_code=72,78,523
        &sum={arr[0]}&board_id={arr[2]}&type_cd={arr[1]}&viewUp=viewUp
        &main_no={arr[3]}&sub_no={arr[4]}&answer_lvl={arr[5]}&check=view
     응답 HTML(EUC-KR) → div.tblAreaV 추출
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

HOST = "https://www.nhqv.com"
LIST_PATH = (
    "/wooriwmBoard/boardList.action?wm_menu_code=72,78,523"
    "&iPg=1&iPageSize=20&sType_Cd=0000000002&sBoard_Id=1"
    "&keyfield=whole&research="
)
DETAIL_PATH = "/wooriwmBoard/boardView.action"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

# viewUp('sum','type_cd','board_id','main_no','sub_no','answer_lvl')
_VIEWUP_RE = re.compile(r"viewUp\(([^)]*)\)")

# 제외 제목 패턴 (JS와 동일)
_EXCLUDE_SUFFIX_RE = re.compile(r"(청약안내|이벤트 당첨자 발표)$")


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등에서 숫자만 추출."""
    return re.sub(r"\D", "", s or "")


def _parse_viewup_params(onclick: str) -> List[str]:
    """onclick 의 viewUp(...) 인자에서 6개 문자열 파라미터 추출."""
    if not onclick:
        return []
    m = _VIEWUP_RE.search(onclick)
    if not m:
        return []
    inner = m.group(1).strip()
    # 좌우 공백/개행 제거
    inner = re.sub(r"[\s\n\t]+", "", inner)
    # 양 끝 따옴표 제거 후 ','으로 분리
    if len(inner) >= 2 and inner[0] in ("'", '"') and inner[-1] in ("'", '"'):
        inner = inner[1:-1]
    parts = re.split(r"['\"]\s*,\s*['\"]", inner)
    return [p.strip() for p in parts]


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

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
        logger.debug(f"[KRST0247] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET (EUC-KR)
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
        list_html = r.content.decode("euc-kr", errors="replace")
    except Exception as e:
        logger.warning(f"[KRST0247] 목록 요청 실패: {e}")
        return []

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("table#dataTable")
    if table is None:
        logger.warning("[KRST0247] 목록 테이블(table#dataTable) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0247] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_title = col_idx("제목")
    idx_date = col_idx("작성일")

    if idx_title < 0 or idx_date < 0:
        logger.warning(f"[KRST0247] 필수 컬럼 없음 (thead={thead_names})")
        return []

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0247] tbody tr 없음")
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
            return re.sub(r"\s+", " ", tds[i].get_text(" ", strip=True)).strip()

        title = cell_text(idx_title)
        if not title:
            continue

        # 제외 패턴
        if _EXCLUDE_SUFFIX_RE.search(title):
            continue

        posted_date = _normalize_date(cell_text(idx_date))

        # onclick에서 viewUp 파라미터 추출
        a = tr.find("a")
        onclick = (a.get("onclick") if a is not None else "") or ""
        params = _parse_viewup_params(onclick)
        if len(params) < 6:
            logger.debug(f"[KRST0247] viewUp 파라미터 부족({title[:30]}): {onclick[:80]}")
            continue

        query = (
            "wm_menu_code=72,78,523"
            f"&sum={params[0]}"
            f"&board_id={params[2]}"
            f"&type_cd={params[1]}"
            f"&viewUp=viewUp&main_no={params[3]}"
            f"&sub_no={params[4]}"
            f"&answer_lvl={params[5]}"
            "&check=view"
        )
        detail_url = f"{HOST}{DETAIL_PATH}?{query}"

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            dt_text = dr.content.decode("euc-kr", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")
            for tag in d_soup(["script", "style"]):
                tag.decompose()

            view = d_soup.select_one("div.tblAreaV")
            if view is not None:
                # 상대경로 이미지 절대경로화
                for img in view.find_all("img"):
                    src = img.get("src") or ""
                    if src and not src.startswith("http"):
                        img["src"] = HOST + src
                detail_html = view.decode_contents().strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0247] 상세 요청 실패({title[:30]}): {e}")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0247] 핸들러 추출 {len(results)}건")
    return results


register("KRST0247", handle)

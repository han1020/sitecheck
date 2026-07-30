"""
KRST0278 - 신한투자증권 (Shinhan Securities)

흐름:
  1) GET https://bbs2.shinhaninvest.com/siw/board/message/list.do
        ?boardName=nNoticeCFM&frameName=noticeCFMFrm&curPage=1
     응답 HTML(EUC-KR) → 세션 쿠키 확보 및 목록 파싱
       - #messageListDiv table thead th → 컬럼 인덱스 결정
         ('번호', '제목', '파일', '등록일', '조회수')
       - #listForm input[name][value] → 폼 파라미터 수집
       - tbody tr 의 <a href="javascript:view('messageId','messageNumber')">
         형식에서 messageId / messageNumber 추출
  2) 상세: GET /siw/board/message/view.do?{form_params}
        + &messageId=...&messageNumber=...
     응답 HTML(EUC-KR) → div.bbs_view_type1 추출
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://bbs2.shinhaninvest.com"
LIST_PATH = (
    "/siw/board/message/list.do"
    "?boardName=nNoticeCFM&frameName=noticeCFMFrm&curPage=1"
)
DETAIL_PATH = "/siw/board/message/view.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

# javascript:view('882153','5107')
_VIEW_RE = re.compile(r"view\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' 등에서 숫자만 추출."""
    return re.sub(r"\D", "", s or "")


def _collect_form(soup: BeautifulSoup, form_selector: str) -> Dict[str, str]:
    """주어진 폼의 input[name][value] 를 dict 로 수집."""
    form_data: Dict[str, str] = {}
    form = soup.select_one(form_selector)
    if form is None:
        return form_data
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        form_data[name] = inp.get("value") or ""
    return form_data


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

    # 1) 목록 GET (EUC-KR)
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
        list_html = r.content.decode("euc-kr", errors="replace")
    except Exception as e:
        logger.warning(f"[KRST0278] 목록 요청 실패: {e}")
        return []

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("#messageListDiv table")
    if table is None:
        logger.warning("[KRST0278] 목록 테이블(#messageListDiv table) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0278] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_title = col_idx("제목")
    idx_date = col_idx("등록일")

    if idx_title < 0 or idx_date < 0:
        logger.warning(f"[KRST0278] 필수 컬럼 없음 (thead={thead_names})")
        return []

    # listForm input 수집 (상세 URL 쿼리스트링 기본값)
    form_data = _collect_form(soup, "#listForm")

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0278] tbody tr 없음")
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

        # view('messageId','messageNumber') 추출 - 행 전체에서 찾음
        row_html = str(tr)
        m = _VIEW_RE.search(row_html)
        if not m:
            logger.debug(f"[KRST0278] view() 파라미터 없음({title[:30]})")
            continue

        message_id = m.group(1).strip()
        message_number = m.group(2).strip()
        if not message_id or not message_number:
            continue

        posted_date = _normalize_date(cell_text(idx_date))

        # 상세 URL 구성 (form 기본값 + messageId/messageNumber 덮어쓰기)
        params = dict(form_data)
        params["messageId"] = message_id
        params["messageNumber"] = message_number
        detail_url = f"{HOST}{DETAIL_PATH}?{urlencode(params)}"

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            dt_text = dr.content.decode("euc-kr", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")
            for tag in d_soup(["script", "style"]):
                tag.decompose()

            view = d_soup.select_one("div.bbs_view_type1")
            if view is not None:
                # 상대경로 이미지 절대경로화
                for img in view.find_all("img"):
                    src = img.get("src") or ""
                    if src and not src.startswith(("http://", "https://")):
                        img["src"] = HOST + (src if src.startswith("/") else "/" + src)
                detail_html = view.decode_contents().strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0278] 상세 요청 실패({title[:30]}): {e}")
            continue

        # 필수 필드 검증 (JS와 동일: 날짜는 8자리 숫자, 본문 비어있지 않음)
        if not posted_date or len(posted_date) != 8 or not posted_date.isdigit():
            logger.debug(f"[KRST0278] 날짜 형식 비정상({title[:30]}): {posted_date!r}")
            continue
        if not detail_html or not body_text:
            logger.debug(f"[KRST0278] 본문 비어있음({title[:30]})")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0278] 핸들러 추출 {len(results)}건")
    return results


register("KRST0278", handle)

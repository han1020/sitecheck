"""
KRST0225 - IBK투자증권 (IBK Securities)

흐름:
  1. GET https://www.ibks.com (초기 쿠키)
  2. POST https://www.ibks.com/notice/notice_list.do
       Body (form-urlencoded):
         search_key=TITLE&search_value=&start_reg_date=&end_reg_date=&current_page=1
       응답: HTML (cp949)
       table.list tbody tr 의 각 행에서 ['번호','구분','제목','등록일','조회수'] 컬럼
       제목 칸 anchor 의 onclick 'ibkisNoticeView('NNN'...)' 에서 seq 추출
  3. 상세: GET https://www.ibks.com/notice/notice_view.do?seq={seq}
       (과거 POST 는 2026-08 부터 error.html 리다이렉트)
       응답: HTML (cp949)
       div.board_view 내용을 detail_html / body_text 로 사용
"""
from __future__ import annotations

import logging
import re
import ssl
from typing import List

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .. import register
from ..base import HandlerResult


class _WeakSSLAdapter(HTTPAdapter):
    """ibks.com 은 약한 cipher 만 지원하므로 SECLEVEL=0 으로 어댑터 생성."""

    def init_poolmanager(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs["ssl_context"] = ctx
        return super().proxy_manager_for(*args, **kwargs)

logger = logging.getLogger(__name__)

HOST = "https://www.ibks.com"
LIST_PATH = "/notice/notice_list.do"
DETAIL_PATH = "/notice/notice_view.do"
REFERER = HOST + "/menu_mainframe.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_SEQ_RE = re.compile(r"ibkisNoticeView\(\s*'([^']+)'")


def _decode_cp949(resp: requests.Response) -> str:
    """응답 바이트를 cp949 → 실패시 utf-8 로 디코드."""
    try:
        return resp.content.decode("cp949", errors="replace")
    except Exception:
        return resp.content.decode("utf-8", errors="replace")


def _extract_seq(a_tag) -> str:
    """제목 anchor 의 onclick/href 에서 ibkisNoticeView('SEQ',...) 의 SEQ 추출."""
    if a_tag is None:
        return ""
    for attr in ("onclick", "href"):
        val = a_tag.get(attr) or ""
        m = _SEQ_RE.search(val)
        if m:
            return m.group(1).strip()
    # 부모 td 의 innerHTML 에서도 검색
    parent = a_tag.parent
    if parent is not None:
        m = _SEQ_RE.search(str(parent))
        if m:
            return m.group(1).strip()
    return ""


def _normalize_date(s: str) -> str:
    """공백/./-// 제거 후 숫자만 남김."""
    return re.sub(r"\D", "", s or "")


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.mount("https://", _WeakSSLAdapter())
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": REFERER,
        "Connection": "keep-alive",
    })

    # 1) 초기 쿠키 확보 (JSESSIONID 등)
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0225] 초기 GET 실패(무시): {e}")

    # 2) 목록 POST (page 1)
    list_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
    }
    list_body = (
        "search_key=TITLE&search_value=&start_reg_date=&end_reg_date=&current_page=1"
    )

    try:
        r = s.post(
            HOST + LIST_PATH,
            data=list_body,
            headers=list_headers,
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0225] 목록 요청 실패: {e}")
        return []

    list_html = _decode_cp949(r)

    try:
        soup = BeautifulSoup(list_html, "lxml")
    except Exception:
        soup = BeautifulSoup(list_html, "html.parser")

    # script / 주석 제거
    for t in soup(["script"]):
        t.decompose()

    table = soup.select_one("table.list")
    if table is None:
        logger.warning("[KRST0225] table.list 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0225] thead 컬럼 없음")
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
        logger.info("[KRST0225] tbody tr 없음")
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
        category = cell_text(idx_cat)  # noqa: F841

        title = ""
        seq = ""
        if 0 <= idx_title < len(tds):
            a = tds[idx_title].find("a")
            if a is not None:
                title = a.get_text(strip=True)
                seq = _extract_seq(a)
            if not title:
                title = tds[idx_title].get_text(strip=True)

        posted_date = _normalize_date(cell_text(idx_date))

        if not title:
            continue
        if not posted_date or len(posted_date) != 8 or not posted_date.isdigit():
            logger.debug(f"[KRST0225] 잘못된 날짜 형식 skip: {title[:30]} / {posted_date}")
            continue
        if not seq:
            logger.debug(f"[KRST0225] seq 추출 실패 skip: {title[:30]}")
            continue

        detail_url = f"{HOST}{DETAIL_PATH}?seq={seq}"

        detail_html = ""
        body_text = ""
        try:
            # 2026-08: 쿼리스트링 seq를 빈 body로 POST 하면 error.html 로 리다이렉트되도록
            # 사이트가 바뀜 → GET 으로 호출해야 상세가 온다
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            dt_text = _decode_cp949(dr)

            try:
                d_soup = BeautifulSoup(dt_text, "lxml")
            except Exception:
                d_soup = BeautifulSoup(dt_text, "html.parser")

            # 불필요 요소 제거
            for sel in [
                "script",
                "form",
                "#header_wrap",
                "#footer_wrap",
                "div.lnb_wrap",
                "p.line_map",
                "div.view_box_t1",
                "div.view_box_t2",
            ]:
                for t in d_soup.select(sel):
                    t.decompose()

            view = d_soup.select_one("div.board_view")
            if view is not None:
                detail_html = str(view).strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0225] 상세 요청 실패({title[:30]}): {e}")
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0225] 핸들러 추출 {len(results)}건")
    return results


register("KRST0225", handle)

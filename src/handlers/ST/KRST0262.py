"""
KRST0262 - iM증권 (구 하이투자증권, iM Securities)

흐름:
  1. GET https://www.imfnsec.com/research/bussiness_indust/re0203.jsp?bid=C_A001
     - 응답 인코딩: EUC-KR
     - 목록 테이블: table.board_01 (thead th: 번호 / 제목 / 등록일 / 조회)
     - 제목 칸 <a href="javascript:view('AID', ...)"> 에서 aid 추출
     - 페이지에 hidden input(name="bid", "sdate", "edate") 존재
  2. 상세: POST https://www.imfnsec.com/research/bussiness_indust/re020301.jsp?bid={bid}
       body: sdate=&edate=&iKey_view=&dist=&aid={aid}&bid={bid}
             &cur_page=1&pen=&iKey=%25%25&broadcastMemberName=
     - 응답 인코딩: EUC-KR
     - 응답 HTML 의 첫 <table> 을 detail_html, table tbody td.notice 텍스트를 body_text 로 사용
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.imfnsec.com"

# imfnsec.com 서버가 TLS 체인에서 중간 인증서(Sectigo EV R36)를 간헐적으로
# 안 보내줘 certifi 기본 검증이 실패한다 → 중간+루트(R46)를 동봉한 번들로 검증.
_CA_BUNDLE = (
    Path(__file__).resolve().parents[3] / "config" / "certs" / "sectigo-imfnsec-chain.pem"
)
LIST_PATH = "/research/bussiness_indust/re0203.jsp?bid=C_A001"
DETAIL_PATH = "/research/bussiness_indust/re020301.jsp"
REFERER = "https://www.hi-ib.com/research/bussiness_indust/re0203.jsp?bid=C_A001"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_VIEW_RE = re.compile(r"view\('([^']+)'")
_HIDDEN_RE = re.compile(
    r"""<input[^>]*name=["']?(?P<name>[A-Za-z0-9_]+)["']?[^>]*value=["']?(?P<value>[^"'>]*)["']?[^>]*/?>""",
    re.IGNORECASE,
)


def _extract_aid(a_tag) -> str:
    """<a> 의 href / onclick 에서 view('AID', ...) 의 AID 추출."""
    if a_tag is None:
        return ""
    for attr in ("href", "onclick"):
        val = a_tag.get(attr) or ""
        m = _VIEW_RE.search(val)
        if m:
            return m.group(1).strip()
    return ""


def _find_hidden_value(html: str, name: str) -> str:
    """원본 HTML 문자열에서 name 속성이 일치하는 첫 hidden input 의 value 추출."""
    for m in _HIDDEN_RE.finditer(html):
        if m.group("name") == name:
            return m.group("value") or ""
    return ""


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    if _CA_BUNDLE.is_file():
        s.verify = str(_CA_BUNDLE)
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 목록 GET (EUC-KR)
    try:
        r = s.get(HOST + LIST_PATH, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0262] 목록 요청 실패: {e}")
        return []

    try:
        list_html = r.content.decode("euc-kr", errors="replace")
    except Exception:
        list_html = r.text

    soup = BeautifulSoup(list_html, "lxml")
    # script 제거 (JS 내부의 view(...) 호출이 BS 텍스트 파싱에 섞이지 않도록)
    for tag in soup.find_all("script"):
        tag.decompose()

    table = soup.select_one("table.board_01")
    if not table:
        logger.warning("[KRST0262] 목록 테이블(table.board_01) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0262] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_no = col_idx("번호")
    idx_title = col_idx("제목")
    idx_date = col_idx("등록일")

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0262] tbody tr 없음 (공지 없음)")
        return []

    # 상세 POST 에 필요한 hidden 값 (원본 HTML 에서 직접 추출)
    bid = _find_hidden_value(list_html, "bid")
    sdate = _find_hidden_value(list_html, "sdate")
    edate = _find_hidden_value(list_html, "edate")

    detail_headers = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://www.hi-ib.com",
        "Referer": REFERER,
        "Connection": "keep-alive",
    }

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

        title = ""
        aid = ""
        if 0 <= idx_title < len(tds):
            a = tds[idx_title].find("a")
            if a is not None:
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
                aid = _extract_aid(a)
            if not title:
                title = re.sub(r"\s+", " ", tds[idx_title].get_text(" ", strip=True)).strip()

        posted_date = re.sub(r"\D", "", cell_text(idx_date))

        if not title or not aid:
            continue

        body_text_form = (
            f"sdate={quote(sdate)}"
            f"&edate={quote(edate)}"
            f"&iKey_view=&dist="
            f"&aid={aid}"
            f"&bid={bid}"
            f"&cur_page=1&pen=&iKey=%25%25&broadcastMemberName="
        )

        detail_url = f"{HOST}{DETAIL_PATH}?bid={bid}"

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(
                detail_url,
                data=body_text_form,
                headers=detail_headers,
                timeout=20,
            )
            dr.raise_for_status()
            try:
                dt_text = dr.content.decode("euc-kr", errors="replace")
            except Exception:
                dt_text = dr.text

            # HTML 주석 제거 (JS 와 동일)
            dt_text = re.sub(r"<!--.*?-->", "", dt_text, flags=re.DOTALL)

            d_soup = BeautifulSoup(dt_text, "lxml")
            for tag in d_soup.find_all("script"):
                tag.decompose()

            table_tag = d_soup.find("table")
            if table_tag is not None:
                detail_html = table_tag.decode_contents()
            else:
                detail_html = dt_text

            notice_td = d_soup.select_one("table tbody td.notice")
            if notice_td is not None:
                # 본문이 이미지뿐이면 body_text를 비워 둔다 — scraper가
                # 빈 본문을 보고 스크린샷+OCR(→감지/검토) 경로로 처리한다.
                body_text = re.sub(
                    r"\s+", " ", notice_td.get_text(" ", strip=True)
                ).strip()
            else:
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0262] 상세 요청 실패({title[:30]}): {e}")
            continue

        if not posted_date or len(posted_date) != 8 or not detail_html:
            # JS 의 유효성 체크와 동일
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0262] 핸들러 추출 {len(results)}건")
    return results


register("KRST0262", handle)

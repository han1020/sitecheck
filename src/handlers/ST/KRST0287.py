"""
KRST0287 - 메리츠증권 (Meritz Securities)

흐름:
  1) GET https://home.imeritz.com/bbs/BbsList.go?bbsGrpId=bascGrp&bbsId=help13nw&listCnt=0&pageNum=1
     → 목록 HTML (table.list_a) 파싱
       thead th: ['번호', '제목', '첨부', '작성자', '등록일', '조회']
       각 row 의 제목 td 내 <a href="..."> 가 상세 링크 (호스트와 결합).
       등록일 td 의 텍스트에서 '.', '-', '/' 제거 → YYYYMMDD
  2) 상세: GET {detail_url} (Referer: 목록 URL)
     상세 HTML 안의 <input name="contentsHtml" value="..."> 의 value 가 본문 HTML.
     해당 값에서 &nbsp;/&amp;nbsp; → 공백 치환 후 BeautifulSoup 으로 본문/텍스트 추출.
"""
from __future__ import annotations

import html
import logging
import re
from typing import List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://home.imeritz.com"
LIST_PATH = "/bbs/BbsList.go?bbsGrpId=bascGrp&bbsId=help13nw&listCnt=0&pageNum=1"
LIST_URL = HOST + LIST_PATH

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_DATE_CLEAN_RE = re.compile(r"[.\-/]")
# <input ... name="contentsHtml" ... value="..."> 의 value 추출 (탐욕적이 되지 않도록 ?)
_CONTENTS_HTML_RE = re.compile(
    r'name="contentsHtml"[^>]*?value="([^"]*)"',
    re.DOTALL,
)
_CONTENTS_HTML_RE_REV = re.compile(
    r'value="([^"]*)"[^>]*?name="contentsHtml"',
    re.DOTALL,
)


def _extract_contents_html(detail_text: str) -> str:
    """상세 HTML 에서 <input name="contentsHtml" value="..."> 의 value 추출."""
    # 1) BeautifulSoup 으로 우선 시도
    try:
        soup = BeautifulSoup(detail_text, "lxml")
        inp = soup.find("input", attrs={"name": "contentsHtml"})
        if inp is not None:
            val = inp.get("value")
            if val:
                return val
    except Exception:
        pass

    # 2) 정규식 fallback (속성 순서가 value=... name=... 이거나 반대 모두 처리)
    m = _CONTENTS_HTML_RE.search(detail_text)
    if m:
        return html.unescape(m.group(1))
    m = _CONTENTS_HTML_RE_REV.search(detail_text)
    if m:
        return html.unescape(m.group(1))
    return ""


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
        try:
            r.encoding = r.apparent_encoding or "utf-8"
            list_html = r.text
        except Exception:
            list_html = r.content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[KRST0287] 목록 요청 실패: {e}")
        return []

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("table.list_a")
    if table is None:
        logger.warning("[KRST0287] 목록 테이블(table.list_a) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0287] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_title = col_idx("제목")
    idx_date = col_idx("등록일")

    if idx_title < 0 or idx_date < 0:
        logger.warning(f"[KRST0287] 필수 컬럼 누락 (thead={thead_names})")
        return []

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0287] tbody tr 없음")
        return []

    results: List[HandlerResult] = []

    # 상세 요청 시 Referer 설정
    detail_headers = {"Referer": LIST_URL}

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds or idx_title >= len(tds) or idx_date >= len(tds):
            continue

        title_td = tds[idx_title]
        a = title_td.find("a")
        if a is None:
            continue

        title = a.get_text(strip=True) or title_td.get_text(strip=True)
        href = a.get("href") or ""
        # JS: href.replace("&amp;", "") - BeautifulSoup 은 이미 디코드 함
        if not href:
            continue
        detail_url = urljoin(HOST + "/", href)

        raw_date = tds[idx_date].get_text(strip=True)
        posted_date = _DATE_CLEAN_RE.sub("", raw_date).strip()

        if not title:
            continue

        # 2) 상세 GET
        detail_html_val = ""
        body_text = ""
        try:
            dr = s.get(detail_url, headers=detail_headers, timeout=20)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            contents_html = _extract_contents_html(dt_text)
            # &nbsp; → 공백
            contents_html = contents_html.replace("&nbsp;", " ").replace(
                "&amp;nbsp;", " "
            ).strip()

            if contents_html:
                d_soup = BeautifulSoup(contents_html, "lxml")
                detail_html_val = contents_html
                body_text = d_soup.get_text("\n", strip=True)
            else:
                # fallback: 전체 페이지에서 script/style 제거 후 사용
                d_soup = BeautifulSoup(dt_text, "lxml")
                for tag in d_soup(["script", "style"]):
                    tag.decompose()
                body_tag = d_soup.body
                if body_tag is not None:
                    detail_html_val = body_tag.decode_contents().strip()
                    body_text = body_tag.get_text("\n", strip=True)
                else:
                    detail_html_val = dt_text
                    body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0287] 상세 요청 실패({title[:30]}): {e}")

        # posted_date 검증 (YYYYMMDD 8자리 숫자)
        if not posted_date or not posted_date.isdigit() or len(posted_date) != 8:
            logger.debug(
                f"[KRST0287] posted_date 형식 이상 ({raw_date!r}) - 건너뜀: {title[:30]}"
            )
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html_val,
            body_text=body_text,
        ))

    logger.info(f"[KRST0287] 핸들러 추출 {len(results)}건")
    return results


register("KRST0287", handle)

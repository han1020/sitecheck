"""
KRST0227 - 다올투자증권 (Daol Securities)

흐름:
  1. GET https://www.daolsecurities.com (초기 쿠키)
  2. POST https://www.daolsecurities.com/customer/center/news.jspx?cmd=list&menuGb=&templet-bypass=true
       Body: curPage=1&preSeq=&bbChnl=&searchType=all&searchStr=
       응답: <tr> 들의 HTML 조각 (table 내부)
       각 row 의 td:
         td[0] = 번호, td[1] = 제목(<a onclick="detail('SEQ',...)">), td[2] = 등록일
  3. 상세: GET /common/editor/editor.jspx?cmd=viewCont&bbSeq={bb_seq}&templet-bypass=true
       응답 HTML 의 #qtCont 영역을 detail_html / body_text 로 사용
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

HOST = "https://www.daolsecurities.com"
LIST_PATH = "/customer/center/news.jspx?cmd=list&menuGb=&templet-bypass=true"
DETAIL_PATH = "/common/editor/editor.jspx?cmd=viewCont&bbSeq={bb_seq}&templet-bypass=true"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_BBSEQ_RE = re.compile(r"detail\(\s*['\"]([^'\"]+)['\"]")


def _extract_bb_seq(td) -> str:
    """제목 td 내 <a onclick="detail('SEQ', ...)"> 에서 SEQ 추출."""
    if td is None:
        return ""
    # onclick / href / 전체 inner html 순으로 탐색
    a = td.find("a")
    if a is not None:
        for attr in ("onclick", "href"):
            val = a.get(attr) or ""
            m = _BBSEQ_RE.search(val)
            if m:
                return m.group(1).strip()
    # fallback: td 의 inner html 전체에서 찾기
    inner = td.decode_contents() if hasattr(td, "decode_contents") else str(td)
    m = _BBSEQ_RE.search(inner or "")
    if m:
        return m.group(1).strip()
    return ""


def _normalize_date(s: str) -> str:
    """공백/하이픈/점/슬래시 제거 후 숫자만 남김."""
    return re.sub(r"\D", "", s or "")


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Connection": "keep-alive",
    })

    # 1) 초기 쿠키 확보
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0227] 초기 GET 실패(무시): {e}")

    # 2) 목록 POST (page 1 만)
    list_headers = {
        "Referer": HOST + "/customer/center/news.jspx",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": HOST,
    }
    list_body = "curPage=1&preSeq=&bbChnl=&searchType=all&searchStr="

    try:
        r = s.post(
            HOST + LIST_PATH,
            data=list_body,
            headers=list_headers,
            timeout=15,
        )
        r.raise_for_status()
        try:
            r.encoding = r.apparent_encoding or "utf-8"
            list_html = r.text
        except Exception:
            list_html = r.content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.warning(f"[KRST0227] 목록 API 실패: {e}")
        return []

    # 응답은 <tr>...</tr> 조각이므로 table 로 감싸 파싱
    wrapped = f"<table>{list_html}</table>"
    try:
        soup = BeautifulSoup(wrapped, "lxml")
    except Exception:
        soup = BeautifulSoup(wrapped, "html.parser")

    rows = soup.select("table tr")
    if not rows:
        logger.warning("[KRST0227] 목록 tr 없음")
        return []

    # 상세 요청용 헤더 (XHR 헤더는 제거)
    detail_headers = {
        "Referer": HOST + "/customer/center/news.jspx",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
    }

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue

        title_td = tds[1]
        a = title_td.find("a")
        title = (a.get_text(strip=True) if a is not None else title_td.get_text(strip=True))
        title = (title or "").strip()

        bb_seq = _extract_bb_seq(title_td)
        posted_date = _normalize_date(tds[2].get_text(strip=True))

        if not title or not bb_seq:
            continue

        detail_url = HOST + DETAIL_PATH.format(bb_seq=bb_seq)

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, headers=detail_headers, timeout=15)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            try:
                d_soup = BeautifulSoup(dt_text, "lxml")
            except Exception:
                d_soup = BeautifulSoup(dt_text, "html.parser")

            cont = d_soup.select_one("#qtCont")
            if cont is not None:
                detail_html = str(cont).strip()
                body_text = cont.get_text("\n", strip=True)
            else:
                # fallback: body 전체
                for tag in d_soup(["script"]):
                    tag.decompose()
                body_tag = d_soup.body
                if body_tag is not None:
                    detail_html = body_tag.decode_contents().strip()
                    body_text = body_tag.get_text("\n", strip=True)
                else:
                    detail_html = dt_text
                    body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0227] 상세 요청 실패({title[:30]}): {e}")
            # 상세 실패시에도 목록 정보는 활용 가능하나, 규약대로 스킵
            continue

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0227] 핸들러 추출 {len(results)}건")
    return results


register("KRST0227", handle)

"""
KRBK0003 - 기업은행 (IBK 기업뱅킹 공지)

흐름 (EUC-KR 인코딩 주의):
  1. GET https://kiup.ibk.co.kr/uib/jsp/index.jsp                      → 쿠키
  2. GET https://kiup.ibk.co.kr/uib/jsp/popup/PNOTICE.jsp?list_yn=N&ebnk_dscd=E
       → 공지 목록 페이지 (EUC-KR)
  3. table.notice_board tbody tr (또는 일반 table tbody tr) 파싱
     - td.subject a (onclick="uf_viewContent('1863')") 에서 seq
  4. POST https://kiup.ibk.co.kr/uib/jsp/popup/PNOTICE.jsp
       Body: pageIndex=1&list_yn=Y&seq=<seq>&keyword=&_DUMMY_INPUT=&HTML_TOKEN_EXCEPT_URL=win_open
  5. 응답에서 div.popup_cont 의 내용 추출
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

HOST = "https://kiup.ibk.co.kr"
HOME_PATH = "/uib/jsp/index.jsp"
LIST_PATH = "/uib/jsp/popup/PNOTICE.jsp?list_yn=N&ebnk_dscd=E"
DETAIL_PATH = "/uib/jsp/popup/PNOTICE.jsp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_VIEW_RE = re.compile(r"uf_viewContent\(\s*['\"]?(\d+)['\"]?\s*\)")


def _decode(resp: requests.Response) -> str:
    # 기업은행 응답은 EUC-KR. 명시적으로 디코드.
    try:
        return resp.content.decode("euc-kr", errors="replace")
    except Exception:
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    try:
        s.get(HOST + HOME_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0003] 워밍업 실패(무시): {e}")

    try:
        r = s.get(HOST + LIST_PATH, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0003] 목록 GET 실패: {e}")
        return []

    html_text = _decode(r)
    soup = BeautifulSoup(html_text, "lxml")
    rows = soup.select("table.notice_board tbody tr") or soup.select("table tbody tr")
    if not rows:
        logger.warning("[KRBK0003] 목록 행 없음")
        return []

    s.headers.update({
        "Referer": HOST + LIST_PATH,
        "Content-Type": "application/x-www-form-urlencoded",
    })

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for tr in rows[:max_items]:
        a = tr.select_one("td.subject a") or tr.select_one("td a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue

        m = _VIEW_RE.search(a.get("onclick") or "")
        if not m:
            continue
        seq = m.group(1)

        tds = tr.find_all("td")
        # 작성일은 td 안에서 'YYYY.MM.DD' 패턴 찾기
        posted_date = ""
        for td in tds:
            t = td.get_text(strip=True)
            if re.fullmatch(r"\d{4}[.\-/]\d{2}[.\-/]\d{2}", t):
                posted_date = re.sub(r"\D", "", t)
                break

        # 상세 POST
        body = (
            f"pageIndex=1&list_yn=Y&seq={seq}&keyword=&_DUMMY_INPUT="
            f"&HTML_TOKEN_EXCEPT_URL=win_open"
        )
        detail_html = ""
        body_text = ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=body, timeout=15)
            dr_text = _decode(dr)
            d_soup = BeautifulSoup(dr_text, "lxml")
            popup = d_soup.select_one("div.popup_cont")
            if popup:
                detail_html = str(popup)
                cont = popup.select_one("td.cont") or popup
                body_text = cont.get_text("\n", strip=True)
            else:
                detail_html = dr_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRBK0003] 상세 POST 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0003] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0003", handle)

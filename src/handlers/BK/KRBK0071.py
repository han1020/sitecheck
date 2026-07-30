"""
KRBK0071 - 우체국 (epostbank)

흐름:
  1. GET https://www.epostbank.go.kr/CCBTNS0000.do                     → 쿠키
  2. POST https://www.epostbank.go.kr/CCBTNS0000R01.do
       Body: q_currPage=1&q_rowPerPage=30&q_pagePerPage=10
       응답 JSON: { noticeList: { list: [...] }, newList: { list: [...] } }
       각 항목: evetNewsSn(번호), evetNewsTitl(제목), regYmd(작성일 YYYYMMDD),
                ibnkMngrVal(상세 호출용 토큰)
  3. POST https://www.epostbank.go.kr/CCBTNS00ASR01.do
       Body: ibnkMngrVal=<val>&evetNewsSn=<sn>
       응답 JSON.evetNewsCn = HTML 본문
"""
from __future__ import annotations

import html as html_lib
import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.epostbank.go.kr"
HOME_PATH = "/CCBTNS0000.do"
LIST_PATH = "/CCBTNS0000R01.do"
DETAIL_PATH = "/CCBTNS00ASR01.do"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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
        logger.debug(f"[KRBK0071] 워밍업 실패(무시): {e}")

    max_items = getattr(site_config, "max_items", 15)
    s.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": HOST + HOME_PATH,
    })

    # 목록 호출
    try:
        r = s.post(
            HOST + LIST_PATH,
            data=f"q_currPage=1&q_rowPerPage={max_items}&q_pagePerPage=10",
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0071] 목록 호출 실패: {e}")
        return []

    items = (data.get("noticeList", {}).get("list") or []) \
            + (data.get("newList", {}).get("list") or [])
    results: List[HandlerResult] = []

    for it in items[:max_items]:
        title = (it.get("evetNewsTitl") or "").strip()
        if not title:
            continue
        posted_date = (it.get("regYmd") or "").strip()
        sn = it.get("evetNewsSn", "")
        mng = it.get("ibnkMngrVal", "")
        if not sn or mng is None:
            continue

        # 상세 호출
        contents_html = ""
        try:
            dr = s.post(
                HOST + DETAIL_PATH,
                data=f"ibnkMngrVal={mng}&evetNewsSn={sn}",
                timeout=15,
            )
            dr.raise_for_status()
            d_data = dr.json()
            contents_html = d_data.get("evetNewsCn") or ""
        except Exception as e:
            logger.debug(f"[KRBK0071] 상세 실패({title[:30]}): {e}")

        # HTML entities decode + text
        if contents_html:
            contents_html = html_lib.unescape(contents_html)
        try:
            soup = BeautifulSoup(contents_html, "lxml")
            for tag in soup.find_all(["style", "script"]):
                tag.decompose()
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = contents_html

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=contents_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0071] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0071", handle)

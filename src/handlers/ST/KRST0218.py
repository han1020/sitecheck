"""
KRST0218 - KB증권 (KB Securities)

흐름:
  1. GET https://www.kbsec.com (초기 쿠키)
  2. POST https://www.kbsec.com/go.able?linkcd=CJTRAJAX2
       Body: sSimPageName=&sSimScrName=%5B공지사항목록조회%5D&
             sSimTrInput=404,B,A,0,,,,,&sSimTrcd=VIS80100&sSimNextGubun=&sSimOp=1&isLoadImgManual=
     응답 JSON.grid: [ [regDate, _, _, seq, _, title, ...], ... ]
     응답 JSON.d[4] = hidNext
  3. 각 공지마다 POST https://www.kbsec.com/go.able?linkcd=s060901010000
       Body: paca_send=&hidNext={hidNext}&idt={regDate}&seq={seq}&searchType=1&searchText=
     상세 HTML(EUC-KR)에서 div.viewCont 추출
"""
from __future__ import annotations

import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.kbsec.com"
LIST_PATH = "/go.able?linkcd=CJTRAJAX2"
DETAIL_PATH = "/go.able?linkcd=s060901010000"
REFERER = HOST + "/go.able?linkcd=m06090001"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

# 페이지 1 (start_no 비어있음)
LIST_BODY = (
    "sSimPageName=&"
    "sSimScrName=%5B%EA%B3%B5%EC%A7%80%EC%82%AC%ED%95%AD%EB%AA%A9%EB%A1%9D%EC%A1%B0%ED%9A%8C%5D&"
    "sSimTrInput=404%2CB%2CA%2C0%2C%2C%2C%2C%2C&"
    "sSimTrcd=VIS80100&sSimNextGubun=&sSimOp=1&isLoadImgManual="
)


def _safe_get(arr, idx, default=""):
    try:
        v = arr[idx]
        return "" if v is None else str(v)
    except (IndexError, TypeError):
        return default


def handle(site_config) -> List[HandlerResult]:
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

    # 1) 초기 쿠키
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0218] 초기 GET 실패(무시): {e}")

    # 2) 목록 POST
    list_headers = {
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }
    try:
        r = s.post(HOST + LIST_PATH, data=LIST_BODY, headers=list_headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRST0218] 목록 API 실패: {e}")
        return []

    grid = data.get("grid") or []
    d_arr = data.get("d") or []
    hid_next = _safe_get(d_arr, 4)

    if not grid:
        logger.warning("[KRST0218] grid 비어있음")
        return []

    # 상세 호출용 헤더 (X-Requested-With 제거)
    detail_headers = {
        "Referer": REFERER,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Content-Type": "application/x-www-form-urlencoded",
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
    }

    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for ele in grid[:max_items]:
        if not isinstance(ele, list):
            continue
        title = _safe_get(ele, 5).strip()
        posted_date = _safe_get(ele, 0).strip()
        seq = _safe_get(ele, 3).strip()
        if not title or not posted_date or not seq:
            continue

        detail_body = (
            f"paca_send=&hidNext={hid_next}&idt={posted_date}"
            f"&seq={seq}&searchType=1&searchText="
        )

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(
                HOST + DETAIL_PATH,
                data=detail_body,
                headers=detail_headers,
                timeout=15,
            )
            dr.raise_for_status()
            dt_text = dr.content.decode("euc-kr", errors="replace")
            d_soup = BeautifulSoup(dt_text, "lxml")
            # 불필요 요소 제거
            for t in d_soup.find_all(["script"]):
                t.decompose()
            for t in d_soup.select("div.viewHead"):
                t.decompose()

            view = d_soup.select_one("div.viewCont")
            if view:
                detail_html = str(view)
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0218] 상세 POST 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH + f"&seq={seq}&idt={posted_date}",
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0218] 핸들러 추출 {len(results)}건")
    return results


register("KRST0218", handle)

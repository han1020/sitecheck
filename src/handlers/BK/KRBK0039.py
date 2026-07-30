"""
KRBK0039 - 경남은행 (BNK KNBank)

흐름:
  1. GET https://www.knbank.co.kr/                              → 쿠키
  2. GET https://www.knbank.co.kr/ib20/mnu/BHPBKI020100000
     응답 HTML. var pageTokenKey = "<TOKEN>" 추출 + #searchFrm input hidden + div.board table tbody tr 행
  3. 각 행: a onclick="fncGoDetail('<seq>','<flag>')" 에서 두 파라미터 추출
  4. POST https://www.knbank.co.kr/ib20/wgt/BHPBBS100DETV20M?ib20_cur_mnu=BHPBKI020100000
     Body: 폼 데이터 + ITG_BRD_DTL_SRNO/ITG_ARTL_MAJ_ANC_YN/REQUEST_TOKEN_KEY/CHECK_TRAN_KEY 등
  5. div.board-view (또는 보드 본문 영역) 추출
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)
HOST = "https://www.knbank.co.kr"
LIST_PATH = "/ib20/mnu/BHPBKI020100000"
DETAIL_PATH = "/ib20/wgt/BHPBBS100DETV20M?ib20_cur_mnu=BHPBKI020100000"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TOKEN_RE = re.compile(r'var\s+pageTokenKey\s*=\s*"([^"]+)"')
_DETAIL_RE = re.compile(r"fncGoDetail\(\s*'([^']*)'\s*,\s*'([^']*)'\s*\)")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        s.get(HOST, timeout=15)
        r = s.get(HOST + LIST_PATH, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[KRBK0039] 목록 실패: {e}")
        return []

    m = _TOKEN_RE.search(r.text)
    if not m:
        logger.warning("[KRBK0039] pageTokenKey 추출 실패")
        return []
    token = m.group(1)

    soup = BeautifulSoup(r.text, "lxml")
    form_data = {
        inp.get("name"): inp.get("value", "")
        for inp in soup.select("#searchFrm input")
        if inp.get("name")
    }
    form_data.pop("headCnt", None)
    form_data.update({
        "BBS_ACTION_TYPE": "S",
        "REQUEST_TOKEN_KEY": token,
        "ib20.persistent.lang.code": "kr",
        "b_page_id": "",
        "inqTITLE": "total",
    })

    rows = soup.select("div.board table tbody tr")
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []
    s.headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": HOST + LIST_PATH,
    })

    for tr in rows[:max_items]:
        tds = tr.find_all("td")
        if len(tds) < 3:
            continue
        # 제목/날짜 셀 찾기
        title_td = None
        for td in tds:
            if td.find("a") is not None:
                title_td = td
                break
        if title_td is None:
            continue
        a = title_td.find("a")
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        m2 = _DETAIL_RE.search(tr.decode_contents())
        if not m2:
            continue
        srno, flag = m2.group(1), m2.group(2)

        posted_date = ""
        for td in tds:
            t = td.get_text(strip=True)
            if re.fullmatch(r"\d{4}[.\-/]\d{2}[.\-/]\d{2}", t):
                posted_date = re.sub(r"\D", "", t)
                break

        body = dict(form_data)
        body.update({
            "ITG_BRD_DTL_SRNO": srno,
            "ITG_ARTL_MAJ_ANC_YN": flag,
            "CHECK_TRAN_KEY": datetime.utcnow().strftime("%Y%m%d%H%M%S"),
        })

        contents_html, body_text = "", ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=urlencode(body), timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            for sel in ["style", "script", "iframe"]:
                for el in d_soup.select(sel):
                    el.decompose()
            # 본문: 다양한 후보
            view = (d_soup.select_one("div.board-view")
                    or d_soup.select_one("div.boardView")
                    or d_soup.select_one("div.viewBox")
                    or d_soup.select_one("div.view-cont"))
            if view:
                contents_html = str(view)
                body_text = view.get_text("\n", strip=True)
            else:
                contents_html = dr.text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRBK0039] 상세 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=contents_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0039] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0039", handle)

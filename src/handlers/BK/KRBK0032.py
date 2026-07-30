"""
KRBK0032 - 부산은행 (BNK Busan)

흐름:
  1. GET https://www.busanbank.co.kr/                            → 쿠키
  2. POST https://www.busanbank.co.kr/ib20/mnu/BHPBKI392002003
       Body: selectmenuid=BHPBKI392002003&ib20.persistent.lang.code=&b_page_id=
     응답 HTML. #contentForm input hidden 모음 + ul.board-thum2 li 행
  3. 각 행의 dt a[seq/itg_ance_mgno/nw_ibnk_biz_dvcd/ibnk_site_dvcd] 추출
  4. POST https://www.busanbank.co.kr/ib20/wgt/BHPBKI392INQV1BM?ib20_cur_mnu=<...>
       Body: form 데이터 + INQ_CND/ITG_ANCE_MGNO/SEQ/NW_IBNK_BIZ_DVCD/IBNK_SITE_DVCD
  5. 응답 HTML의 div.board-view-cont 본문
"""
from __future__ import annotations

import logging
from typing import List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)
HOST = "https://www.busanbank.co.kr"
LIST_PATH = "/ib20/mnu/BHPBKI392002003"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0032] 워밍업 실패(무시): {e}")

    s.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = s.post(HOST + LIST_PATH,
                   data="selectmenuid=BHPBKI392002003&ib20.persistent.lang.code=&b_page_id=",
                   timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
    except Exception as e:
        logger.warning(f"[KRBK0032] 목록 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")
    form_data = {
        inp.get("name"): inp.get("value", "")
        for inp in soup.select("#contentForm input")
        if inp.get("name")
    }
    if not form_data.get("ib20_cur_mnu"):
        logger.warning("[KRBK0032] ib20_cur_mnu 누락")
        return []
    form_data["ib20_change_wgt"] = "BHPBKI392INQV1BM"
    detail_url = (HOST + "/ib20/wgt/BHPBKI392INQV1BM?ib20_cur_mnu="
                  + form_data["ib20_cur_mnu"])

    items = soup.select("ul.board-thum2 li")
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []
    s.headers["Referer"] = HOST + LIST_PATH

    for li in items[:max_items]:
        a = li.select_one("dt a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        if not title:
            continue
        seq = a.get("seq") or ""
        itg = a.get("itg_ance_mgno") or ""
        nwd = a.get("nw_ibnk_biz_dvcd") or ""
        site_dv = a.get("ibnk_site_dvcd") or ""
        if not (seq and itg and nwd and site_dv):
            continue
        date_el = li.select_one("span.contl-opt")
        posted_date = (date_el.get_text(strip=True).replace("-", "")
                       if date_el else "")

        body = dict(form_data)
        body.update({
            "INQ_CND": "1", "ITG_ANCE_MGNO": itg, "SEQ": seq,
            "NW_IBNK_BIZ_DVCD": nwd, "NW_IBNK_BIZ_DVCD_DV": "ALL",
            "IBNK_SITE_DVCD": site_dv, "INQ_CNTN": "",
            "ibs_current_page": "1", "b_page_id": "",
        })

        contents_html, body_text = "", ""
        try:
            dr = s.post(detail_url, data=urlencode(body), timeout=15)
            dr.encoding = dr.apparent_encoding or "utf-8"
            d_soup = BeautifulSoup(dr.text, "lxml")
            view = d_soup.select_one("div.board-view-cont")
            if view:
                for t in view.find_all(["style", "script"]):
                    t.decompose()
                contents_html = str(view)
                body_text = view.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRBK0032] 상세 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=detail_url,
            detail_html=contents_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0032] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0032", handle)

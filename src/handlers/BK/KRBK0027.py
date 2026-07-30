"""
KRBK0027 - 씨티은행 (Citi Direct)

흐름:
  1. GET https://koreacitidirect.citigroup.com/                       → 쿠키
  2. POST https://koreacitidirect.citigroup.com/FWK999905g.jct
       Body: _JSON_=<double-url-encoded>
     응답 JSON.REC: [ { TIT, RGDD, RG_TSEQ, FL_APD_YN, FL_TP, ... } ]
  3. 각 항목 상세 본문은 POST /TET001004v1.jct 로 별도 호출
     응답 JSON.CNTN 에 HTML 본문
"""
from __future__ import annotations

import json
import logging
from typing import List
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://koreacitidirect.citigroup.com"
LIST_PATH = "/FWK999905g.jct"
DETAIL_PATH = "/TET001004v1.jct"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _encode_body(obj: dict) -> str:
    return "_JSON_=" + quote(quote(json.dumps(obj), safe=""), safe="")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    try:
        s.get(HOST + "/", timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0027] 워밍업 실패(무시): {e}")

    max_items = getattr(site_config, "max_items", 15)
    list_obj = {
        "PAGE_NO": "1",
        "REC_CN": str(max_items),
        "LANG_DS": "K", "SRCH_DS": "TITLE", "SRCH_VAL": "",
        "DTI_BSN_C": "2", "STS_DS": "1", "__CSRF_TOKEN__": "",
    }
    s.headers.update({
        "Referer": HOST + "/FWK999905v.act",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/plain, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    try:
        r = s.post(HOST + LIST_PATH, data=_encode_body(list_obj), timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0027] 목록 호출 실패: {e}")
        return []

    rec = data.get("REC") or []
    results: List[HandlerResult] = []

    for ele in rec[:max_items]:
        title = (ele.get("TIT") or "").strip()
        if not title:
            continue
        posted_date = (ele.get("RGDD") or "").strip()

        # 상세 호출
        detail_obj = {
            "RGDD": posted_date,
            "RG_TSEQ": ele.get("RG_TSEQ", ""),
            "FL_APD_YN": ele.get("FL_APD_YN", ""),
            "TBL_KD": ele.get("TBL_KD", ""),
            "FL_TP": ele.get("FL_TP", ""),
            "__CSRF_TOKEN__": "",
        }
        contents_html = ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=_encode_body(detail_obj), timeout=15)
            dr.raise_for_status()
            d_data = dr.json()
            contents_html = d_data.get("CNTN") or ""
        except Exception as e:
            logger.debug(f"[KRBK0027] 상세 호출 실패({title[:30]}): {e}")

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

    logger.info(f"[KRBK0027] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0027", handle)

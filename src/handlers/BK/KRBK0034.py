"""
KRBK0034 - 광주은행 (KJBank)

흐름:
  1. GET https://www.kjbank.com/ib20/mnu/BHPBKIF050101          → 쿠키
  2. POST https://www.kjbank.com/ib20/act/BHPBKIF050101A10?ib20_cur_mnu=BHPBKIF050101&ib20_cur_wgt=BHPBKIF050101V10
     응답 JSON._msg_._body_.LOOP: [ { POPUP_TITL, REG_DT, POPUP_CTNT(HTML), BLTHG_SEQNO } ]
     POPUP_TITL / POPUP_CTNT 는 URL-encoded + HTML entities 이중 인코딩
"""
from __future__ import annotations

import html as html_lib
import logging
from typing import List
from urllib.parse import unquote_plus

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)
HOST = "https://www.kjbank.com"
LIST_PATH = "/ib20/act/BHPBKIF050101A10?ib20_cur_mnu=BHPBKIF050101&ib20_cur_wgt=BHPBKIF050101V10"
HOME_PATH = "/ib20/mnu/BHPBKIF050101"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _decode_field(v: str) -> str:
    if not v:
        return ""
    v = unquote_plus(v)
    v = html_lib.unescape(v.replace("&#59;", ";"))
    return v


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        s.get(HOST + HOME_PATH, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0034] 워밍업 실패(무시): {e}")

    s.headers.update({"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = s.post(HOST + LIST_PATH, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"[KRBK0034] 목록 실패: {e}")
        return []

    loop = (data.get("_msg_", {}).get("_body_", {}).get("LOOP")
            or data.get("LOOP") or [])
    max_items = getattr(site_config, "max_items", 15)
    results: List[HandlerResult] = []

    for ele in loop[:max_items]:
        title = _decode_field(ele.get("POPUP_TITL") or "").strip()
        if not title:
            continue
        posted_date = (ele.get("REG_DT") or "").strip()
        contents_html = _decode_field(ele.get("POPUP_CTNT") or "")

        try:
            soup = BeautifulSoup(contents_html, "lxml")
            for t in soup.find_all(["style", "script"]):
                t.decompose()
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = contents_html

        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=HOST + LIST_PATH,
            detail_html=contents_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0034] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0034", handle)

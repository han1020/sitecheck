"""
KRBK0031 - 대구은행 (iM뱅크)

흐름:
  1. GET https://www.dgb.co.kr/                                            → 쿠키
  2. POST https://www.dgb.co.kr/bbs_ebz_10010_bord_d002.jct
       Body: _JSON_=<encoded>&aap_v=
     응답 JSON.REC1: [ { TIT_NM, REG_DTTI, BBS_ID, PUTUP_WRIT_SEQ } ]
  3. POST https://www.dgb.co.kr/bbs_ebz_10020_bord_d001.jct
       Body: _JSON_=<encoded { BBS_ID, PUTUP_WRIT_SEQ, ... }>&aap_v=
     응답 JSON.REC1[0].PUTUP_WRIT_CN = HTML 본문
"""
from __future__ import annotations

import json
import logging
from typing import List
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)
HOST = "https://www.dgb.co.kr"
LIST_PATH = "/bbs_ebz_10010_bord_d002.jct"
DETAIL_PATH = "/bbs_ebz_10020_bord_d001.jct"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRBK0031] 워밍업 실패(무시): {e}")

    max_items = getattr(site_config, "max_items", 15)
    s.headers.update({
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": HOST + "/bbs_ebz_10010_bord.act",
    })
    list_payload = {
        "BBS_ID": "NEW001", "PUTUP_SITE_DVCD": "03", "PUTUP_MNU_DVCD": "A",
        "ESYCT_WRIT_DVCD": "", "ALL_USE_YN": "", "BSNS_YR": "",
        "INQ_SDT": None, "INQ_EDT": None,
        "BBS_SPPT_SV_DVCD": "", "BANK_TRUST_DVCD": "",
        "FST_CHAR": "", "FST_KOR": "",
        "BBS_INQ_DVCD": "01", "BBS_INQ_CN": "",
        "EBZ_WEB_WORK_COMM": {"INQ_SEQ": "1", "INQ_NCSE": max_items},
    }
    list_body = urlencode({"_JSON_": json.dumps(list_payload, ensure_ascii=False), "aap_v": ""})
    try:
        r = s.post(HOST + LIST_PATH, data=list_body, timeout=15)
        r.raise_for_status()
        rec = r.json().get("REC1") or []
    except Exception as e:
        logger.warning(f"[KRBK0031] 목록 호출 실패: {e}")
        return []

    s.headers["Referer"] = HOST + "/bbs_ebz_10020_bord.act"
    results: List[HandlerResult] = []

    for ele in rec[:max_items]:
        title = (ele.get("TIT_NM") or "").strip()
        if not title:
            continue
        posted_date = (ele.get("REG_DTTI") or "").replace(".", "").strip()

        detail_payload = {
            "BBS_ID": ele.get("BBS_ID"),
            "PUTUP_WRIT_SEQ": int(ele.get("PUTUP_WRIT_SEQ", 0)),
            "UPPER_PUTUP_WRIT_SNO": "",
            "BBS_DTL_TPCD": "07",
        }
        dt_body = urlencode({"_JSON_": json.dumps(detail_payload), "aap_v": ""})
        contents_html = ""
        try:
            dr = s.post(HOST + DETAIL_PATH, data=dt_body, timeout=15)
            dr.raise_for_status()
            drec = dr.json().get("REC1") or []
            if drec:
                contents_html = (drec[0].get("PUTUP_WRIT_CN") or "").strip()
        except Exception as e:
            logger.debug(f"[KRBK0031] 상세 실패({title[:30]}): {e}")

        try:
            soup = BeautifulSoup(contents_html, "lxml")
            for t in soup.find_all(["style", "script"]):
                t.decompose()
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = contents_html

        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=contents_html, body_text=body_text,
        ))

    logger.info(f"[KRBK0031] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0031", handle)

"""
KRBK0103 - SBI저축은행 (공지사항)

흐름 (암호화 JSON API, AES-256-CBC, 원본 스크래퍼 그대로 포팅):
  1. GET https://www.sbisb.co.kr/  → 쿠키
  2. POST /cum0020300A01.jct  body: _JSON_={"PAGE_NO":1,"PAGE_LINE_CCNT":10,"BRD_DSTC_VAL":"IBANK"}
     응답: hex 문자열 → AES-256-CBC 복호화 → JSON (REC[])
  3. POST /cum0020400A01.jct  body: _JSON_={ROW_NO, BRD_SRNO, ...}  → 복호화 → BRD_CNTN(HTML)

복호화: key = "91a8a638151902f63cb4abc1c92d9ce4"(32바이트 UTF-8 그대로), IV = 0x00*16, PKCS7.
"""
from __future__ import annotations

import json
import logging
from typing import List

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.sbisb.co.kr"
LIST_URL = HOST + "/cum0020300A01.jct"
DETAIL_URL = HOST + "/cum0020400A01.jct"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_KEY = b"91a8a638151902f63cb4abc1c92d9ce4"
_IV = bytes(16)


def _decrypt(hex_text: str) -> dict:
    raw = bytes.fromhex(hex_text.strip())
    dec = unpad(AES.new(_KEY, AES.MODE_CBC, _IV).decrypt(raw), 16)
    return json.loads(dec.decode("utf-8"))


def _text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for st in soup.find_all("style"):
        st.decompose()
    return soup.get_text("\n", strip=True)


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Connection": "keep-alive",
    })
    try:
        s.get(HOST + "/", timeout=20)
        r = s.post(
            LIST_URL,
            data='_JSON_={"PAGE_NO":1,"PAGE_LINE_CCNT":10,"BRD_DSTC_VAL":"IBANK"}',
            timeout=20,
        )
        r.raise_for_status()
        items = _decrypt(r.text).get("REC") or []
    except Exception as e:
        logger.warning(f"[KRBK0103] 목록 조회/복호화 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in items:
        title = (it.get("BRD_TTL") or "").strip()
        if not title:
            continue
        posted_date = str(it.get("REG_MRK_DT") or "").strip()[:8]
        payload = {
            "ROW_NO": it.get("ROW_NO"), "BRD_SRNO": it.get("BRD_SRNO"),
            "SRCH_CNTN": None, "SRCH_DVCD": None, "PAGE_LINE_CCNT": 10,
            "BRD_DSTC_VAL": "IBANK", "ATFL_DSTC_VAL": "IBANK",
        }
        try:
            dr = s.post(DETAIL_URL,
                        data="_JSON_=" + json.dumps(payload, separators=(",", ":")),
                        timeout=20)
            dr.raise_for_status()
            detail = _decrypt(dr.text)
            content_html = (detail.get("REC") or [detail])[0].get("BRD_CNTN") \
                if isinstance(detail.get("REC"), list) and detail.get("REC") \
                else detail.get("BRD_CNTN") or ""
        except Exception as e:
            logger.debug(f"[KRBK0103] 상세 실패(srno={it.get('BRD_SRNO')}): {e}")
            continue
        if not (content_html or "").strip():
            continue

        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=DETAIL_URL,
            detail_html=content_html, body_text=_text(content_html),
        ))

    logger.info(f"[KRBK0103] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0103", handle)

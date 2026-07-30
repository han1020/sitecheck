"""
KRBK0107 - 푸른저축은행 (공지사항)

흐름 (EXAFE E2E 암호화 JSON API, 원본 스크래퍼 그대로 포팅):
  1. GET /  → (authorization 헤더 캡처; 요청엔 미사용)
  2. POST /itb/abo/prc/selectNoticeList.do
       body: {"ds_searchP.page_no": <고정 EXAFE 토큰=1>,
              "ds_searchP.page_size": <고정 EXAFE 토큰=10>,
              "ds_searchP.searchTy":"", "ds_searchP.searchText":""}
     → dlt_noticeList[]  (no=평문, sn=평문 상세키, sj=암호화 제목, rgsde=암호화 날짜)
  3. POST /itb/abo/prc/selectNotice.do  {"ds_param":{"sn":<sn>}}
     → ds_noticeDtl.cn (암호화 HTML)

복호화(getDecData): "__EXAFE_E2E__<base64(a|b)>|<encKey2_b64>|<finalEnc_b64>"
  - a|b: a=HMAC 키 ASCII, b=ms 타임스탬프(토큰에 내장 → 결정적)
  - OTP = HOTP-SHA1(key=a, counter=floor(b/30000)) 6자리
  - key1 = OTP를 16자로 늘린 ASCII (16바이트)
  - key2 = AES-128-CBC(key1, IV=0)로 encKey2 복호화한 것의 앞 16바이트
           (Node decipher.update()가 마지막 블록을 보류하는 동작과 동일)
  - 평문 = AES-128-CBC(key2, IV=0)로 finalEnc 복호화 (PKCS7 unpad)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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

HOST = "https://www.prsb.co.kr"
LIST_URL = HOST + "/itb/abo/prc/selectNoticeList.do"
DETAIL_URL = HOST + "/itb/abo/prc/selectNotice.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_IV = bytes(16)

# 페이지 조회용 고정 EXAFE 토큰 (page_no=1, page_size=10)
_PAGE_NO = ("__EXAFE_E2E__VE9LREpHWEhJTFBFSU9FUXwxNjgxMTkxMzM3NTE1"
            "|fxtPs07e9cjO6BF3Jk4pv36ZynT6J+7cTnn5fJp/dp8=|dvRzs1L0iuZ8ljPurWwvuQ==")
_PAGE_SIZE = ("__EXAFE_E2E__V0pQR0VET0pGRkpPT1dYS3wxNjgxMTkxMzM3NTE2"
              "|XXPW255d5qzzdLi+RXmMBSc7CGutN5UKPEv2L7K4sVk=|4YPfek09taea8X1ooFPdmA==")


def _gen_otp(value1: str, value2: str) -> str:
    counter = int(value2) // 30000  # floor(ms/1000/30)
    ctr_hex = ("0000000000000000" + format(counter, "x"))[-16:]
    digest = hmac.new(value1.encode("ascii"), bytes.fromhex(ctr_hex), hashlib.sha1).digest()
    off = digest[-1] & 0xF
    binary = (
        (digest[off] & 0x7F) << 24
        | (digest[off + 1] & 0xFF) << 16
        | (digest[off + 2] & 0xFF) << 8
        | (digest[off + 3] & 0xFF)
    ) % 1000000
    return str(binary).zfill(6)


def _dec(enc: str) -> str:
    """EXAFE E2E 암호문 → 평문."""
    p0, enc_key2_b64, final_b64 = enc.split("|")
    p0 = p0.replace("__EXAFE_E2E__", "")
    a, b = base64.b64decode(p0).decode().split("|")
    otp = _gen_otp(a, b)
    key1 = "".join(otp[i % len(otp)] for i in range(16)).encode("ascii")
    # encKey2 복호화 후 앞 16바이트 (Node update-only 동작)
    key2 = AES.new(key1, AES.MODE_CBC, _IV).decrypt(base64.b64decode(enc_key2_b64))[:16]
    plain = unpad(AES.new(key2, AES.MODE_CBC, _IV).decrypt(base64.b64decode(final_b64)), 16)
    return plain.decode("utf-8")


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    try:
        s.get(HOST + "/", timeout=20)
        inp = {
            "ds_searchP.page_no": _PAGE_NO,
            "ds_searchP.page_size": _PAGE_SIZE,
            "ds_searchP.searchTy": "",
            "ds_searchP.searchText": "",
        }
        r = s.post(LIST_URL, data=json.dumps(inp), timeout=20)
        r.raise_for_status()
        items = r.json().get("dlt_noticeList") or []
    except Exception as e:
        logger.warning(f"[KRBK0107] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in items:
        try:
            title = _dec(it.get("sj") or "").strip()
            posted_date = _dec(it.get("rgsde") or "").replace("-", "").strip()[:8]
        except Exception as e:
            logger.debug(f"[KRBK0107] 제목/날짜 복호화 실패: {e}")
            continue
        if not title:
            continue
        sn = it.get("sn")

        try:
            dr = s.post(DETAIL_URL, data=json.dumps({"ds_param": {"sn": sn}}), timeout=20)
            dr.raise_for_status()
            enc_cn = (dr.json().get("ds_noticeDtl") or {}).get("cn") or ""
            content_html = _dec(enc_cn) if enc_cn else ""
        except Exception as e:
            logger.debug(f"[KRBK0107] 상세 복호화 실패(sn={sn}): {e}")
            continue
        if not content_html.strip():
            continue

        soup = BeautifulSoup(content_html, "lxml")
        for st in soup.find_all("style"):
            st.decompose()
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=DETAIL_URL,
            detail_html=content_html, body_text=soup.get_text("\n", strip=True),
        ))

    logger.info(f"[KRBK0107] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0107", handle)

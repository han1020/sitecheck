"""
KRBK0109 - DB저축은행 (공지사항)

흐름 (JSON API, 본문이 목록 응답에 포함, 원본 스크래퍼 그대로 포팅):
  1. POST /itb/abo/prc/selectNoticeList.do  Content-Type application/json
       body: {"ds_param":{"searchTy":"","searchText":"","page":"1","rows":"10","sn":"","device":"pc"}}
     → ds_noticeList[]  (sj/rgsde/cn)  ※ 0107(푸른)과 같은 경로지만 평문
  상세 호출 없음 - cn 이 본문
  ※ 2026-08 사이트 개편 후 서버가 SHA-1 계열 서명만 지원 → SECLEVEL=1 어댑터 필요
"""
from __future__ import annotations

import json
import logging
import re
import ssl
from typing import List

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)


class _WeakSSLAdapter(HTTPAdapter):
    """idbsb.com 은 구식 서명 알고리즘만 지원하므로 SECLEVEL=1 로 어댑터 생성."""

    def _ctx(self) -> ssl.SSLContext:
        ctx = create_urllib3_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    def init_poolmanager(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["ssl_context"] = self._ctx()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["ssl_context"] = self._ctx()
        return super().proxy_manager_for(*args, **kwargs)

HOST = "https://www.idbsb.com"
LIST_URL = HOST + "/itb/abo/prc/selectNoticeList.do"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_PAYLOAD = {"ds_param": {"searchTy": "", "searchText": "", "page": "1",
                         "rows": "10", "sn": "", "device": "pc"}}


def _text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for st in soup.find_all("style"):
        st.decompose()
    return soup.get_text("\n", strip=True)


def handle(site_config) -> List[HandlerResult]:
    s = requests.Session()
    s.mount("https://", _WeakSSLAdapter())
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": 'application/json; charset="UTF-8"',
        "Accept": "application/json, text/plain, */*",
        "Connection": "keep-alive",
    })
    try:
        r = s.post(LIST_URL, data=json.dumps(_PAYLOAD), timeout=20)
        r.raise_for_status()
        items = r.json().get("ds_noticeList") or []
    except Exception as e:
        logger.warning(f"[KRBK0109] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for it in items:
        title = (it.get("sj") or "").strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-/]", "", str(it.get("rgsde") or "")).strip()[:8]
        content_html = it.get("cn") or ""
        if not content_html.strip():
            continue
        results.append(HandlerResult(
            title=title, posted_date=posted_date, detail_url=LIST_URL,
            detail_html=content_html, body_text=_text(content_html),
        ))

    logger.info(f"[KRBK0109] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0109", handle)

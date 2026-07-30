"""
KRBK0035 - 제주은행 (인터넷뱅킹 공지사항)

흐름 (eversafe 암호화 POST API, 원본 스크래퍼 그대로 포팅):
  1. GET  https://bank.jejubank.co.kr:6443/                          → 워밍업
  2. POST /inbank/footer.do?evfw=v                                   → 암호화 스크립트 수신
  3. 외부 암호화 서비스(enctool.goweve.io)에 {scriptText, targetUrl, plain}
     전달 → 암호화된 본문(result) 수신
  4. POST /inbank/pr/cc/selListCmkn.do  (암호화 본문)                → 공지 목록 JSON
  5. 각 공지마다 cmknMtrSeqno 로 동일하게 암호화 → POST /inbank/pr/cc/selCmkn.do
       → cmknMtrVO.cmknMtrCtnt (상세 HTML)

주의: 본문 암호화를 외부 서비스(enctool.goweve.io, Bearer 토큰)에 위임합니다.
      이 서비스/토큰이 만료되면 핸들러가 동작하지 않습니다.
"""
from __future__ import annotations

import json
import logging
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://bank.jejubank.co.kr:6443"
LIST_PATH = "/inbank/pr/cc/selListCmkn.do"
DETAIL_PATH = "/inbank/pr/cc/selCmkn.do"
SCRIPT_PATH = "/inbank/footer.do?evfw=v"

ENC_URL = "https://enctool.goweve.io/api/eversafe/v1"
ENC_TOKEN = "a7bb307ec3a949a78fed11dd6eaaa658"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)

# 상세(암호화+POST)를 호출하기 전, 제목에 점검 힌트가 있는 공지만 추리기 위한
# 가벼운 사전 필터. (실제 점검 판별은 scraper의 키워드 매칭이 담당)
_TITLE_HINTS = ("점검", "중단", "중지", "작업", "이용제한", "거래중단", "maintenance")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def _encrypt(script_text: str, target_url: str, plain: str) -> str:
    """외부 eversafe 암호화 서비스로 plain 본문을 암호화하여 result 문자열 반환."""
    enc_json = {
        "scriptText": script_text,
        "targetUrl": target_url,
        "plain": plain,
    }
    r = requests.post(
        ENC_URL,
        data=json.dumps(enc_json),
        headers={
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Connection": "keep-alive",
            "Authorization": f"Bearer {ENC_TOKEN}",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["result"]


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    # 1) 워밍업 + 2) 암호화 스크립트
    try:
        s.get(HOST + "/", timeout=20)
        script_text = s.post(HOST + SCRIPT_PATH, timeout=20).text
    except Exception as e:
        logger.warning(f"[KRBK0035] 워밍업/스크립트 수신 실패: {e}")
        return []

    # 3) 목록 본문 암호화 → 4) 목록 조회
    list_plain = json.dumps({
        "page": "1",
        "schKeyword": "",
        "ipinsideData": {
            "ipinsideData": "", "ipinsideNAT": "", "ipinsideCOMM": "",
        },
    })
    try:
        enc_body = _encrypt(script_text, HOST + LIST_PATH, list_plain)
        s.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        r = s.post(HOST + LIST_PATH, data=enc_body, timeout=30)
        r.raise_for_status()
        sel_list = r.json().get("selList") or []
    except Exception as e:
        logger.warning(f"[KRBK0035] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []

    for item in sel_list:
        title = (item.get("cmknMtrTitl") or "").strip()
        if not title:
            continue
        # 점검 힌트 없는 공지는 상세(암호화+POST) 호출 생략
        if not any(h in title for h in _TITLE_HINTS):
            continue

        seqno = item.get("cmknMtrSeqno")
        posted_date = (item.get("frstRegDttm") or "").replace("-", "")

        # 5) 상세 본문 암호화 → 상세 조회
        try:
            detail_plain = json.dumps({
                "cmknMtrSeqno": seqno,
                "ipinsideData": {
                    "ipinsideData": "", "ipinsideNAT": "", "ipinsideCOMM": "",
                },
            })
            enc_detail = _encrypt(script_text, HOST + DETAIL_PATH, detail_plain)
            dr = s.post(HOST + DETAIL_PATH, data=enc_detail, timeout=30)
            dr.raise_for_status()
            ctnt_html = (dr.json().get("cmknMtrVO") or {}).get("cmknMtrCtnt") or ""
        except Exception as e:
            logger.debug(f"[KRBK0035] 상세 조회 실패(seqno={seqno}): {e}")
            continue

        if not ctnt_html.strip():
            continue

        try:
            soup = BeautifulSoup(ctnt_html, "lxml")
            for st in soup.find_all("style"):
                st.decompose()
            body_text = soup.get_text("\n", strip=True)
        except Exception:
            body_text = ctnt_html

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=HOST + DETAIL_PATH,
            detail_html=ctnt_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0035] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0035", handle)

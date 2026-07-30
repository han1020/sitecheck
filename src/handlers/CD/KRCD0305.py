"""
KRCD0305 - 비씨카드 (BC Card 기업사이트)

2026-04 사이트 리뉴얼로 wisebiz.bccard.com → corp.bccard.com (컨텍스트 /app/wisebiz)
전면 이전. 목록 API·요청형식·응답구조·상세 로딩 방식이 모두 바뀌어 재작성.

흐름:
  1) GET  /app/wisebiz/NewsInfoInq             (세션 쿠키 확보)
  2) POST /app/wisebiz/NewsInfoList            ← ComAjax.request
     - 요청: JSON body {"pageNo":1, "pageBlock":N}  (Content-Type: application/json)
     - 응답: {"status":{...}, "resData":{"total":N, "list":[
                 {"CMTY_SEQ_NO":2065, "ATON":"2026.05.08",
                  "CMTY_TITL":"...", "CTG_NM":"공지/뉴스"}, ...]}}
  3) 상세 GET /app/wisebiz/NewsInfoDetail?seqNo={CMTY_SEQ_NO}
     - 본문은 서버 렌더링 안 됨. 인라인 스크립트의
         var noticeUrl = "/html/cscenter/news/notice_YYYYMMDD_nn.html";
       를 파싱해야 실제 본문 위치를 알 수 있음 ($("#notice-content-area").load()).
  4) GET {noticeUrl}  → 실제 공지 본문 HTML (점검 일시 텍스트 포함)
"""
from __future__ import annotations

import json
import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://corp.bccard.com"
INQ_PATH = "/app/wisebiz/NewsInfoInq"
LIST_PATH = "/app/wisebiz/NewsInfoList"
DETAIL_PATH = "/app/wisebiz/NewsInfoDetail"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 인라인 스크립트: var noticeUrl = "\/html\/cscenter\/news\/notice_20260507_01.html";
_NOTICE_URL_RE = re.compile(r'var\s+noticeUrl\s*=\s*"([^"]+)"')


def _normalize_date(s: str) -> str:
    """'2026.05.08' / '2026-05-08' → '20260508'."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:8] if len(digits) >= 8 else digits


def _fetch_body(s: requests.Session, seq: str, referer: str) -> tuple[str, str]:
    """상세 페이지에서 noticeUrl을 찾아 실제 본문 HTML/텍스트를 반환.

    반환: (detail_html, body_text)
    """
    detail_url = f"{HOST}{DETAIL_PATH}?seqNo={seq}"
    try:
        dr = s.get(detail_url, headers={"Referer": referer}, timeout=15)
        dr.raise_for_status()
    except Exception as e:
        logger.debug(f"[KRCD0305] 상세 요청 실패(seq={seq}): {e}")
        return "", ""

    page = dr.content.decode("utf-8", errors="replace")

    m = _NOTICE_URL_RE.search(page)
    notice_url = (m.group(1).replace("\\/", "/") if m else "").strip()

    # noticeUrl이 없거나 '-'/'' 이면 본문 파일이 없는 공지 → 상세 셸의 제목 영역만 사용
    if not notice_url or notice_url in ("-", "null"):
        d_soup = BeautifulSoup(page, "lxml")
        for tag in d_soup(["script", "style"]):
            tag.decompose()
        view = d_soup.select_one("div.customer-noticeView")
        if view is not None:
            return str(view).strip(), view.get_text("\n", strip=True)
        return "", ""

    body_url = notice_url if notice_url.startswith("http") else f"{HOST}{notice_url}"
    try:
        br = s.get(body_url, headers={"Referer": detail_url}, timeout=15)
        br.raise_for_status()
    except Exception as e:
        logger.debug(f"[KRCD0305] 본문 파일 요청 실패(seq={seq}, {body_url}): {e}")
        return "", ""

    b_soup = BeautifulSoup(br.content.decode("utf-8", errors="replace"), "lxml")
    for tag in b_soup(["script", "style"]):
        tag.decompose()
    body_text = b_soup.get_text("\n", strip=True)
    detail_html = str(b_soup.body or b_soup).strip()
    return detail_html, body_text


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 공지 페이지 GET → 세션 쿠키 확보
    try:
        s.get(HOST + INQ_PATH, timeout=20)
    except Exception as e:
        logger.debug(f"[KRCD0305] 초기 GET 실패(무시): {e}")

    # 2) 목록 AJAX POST (JSON body) → resData.list
    try:
        r = s.post(
            HOST + LIST_PATH,
            data=json.dumps({"pageNo": 1, "pageBlock": max_items}),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": HOST + INQ_PATH,
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRCD0305] 목록 요청 실패: {e}")
        return []

    try:
        payload = json.loads(r.content.decode("utf-8", errors="replace"))
    except Exception as e:
        logger.warning(f"[KRCD0305] 목록 JSON 파싱 실패: {e}")
        return []

    status = payload.get("status") or {}
    if not status.get("success"):
        logger.warning(f"[KRCD0305] 목록 응답 실패: {status.get('message')}")
        return []

    items = ((payload.get("resData") or {}).get("list")) or []
    if not items:
        logger.info("[KRCD0305] 목록 항목 없음")
        return []

    results: List[HandlerResult] = []

    for item in items:
        if len(results) >= max_items:
            break

        title = (item.get("CMTY_TITL") or "").strip()
        seq = str(item.get("CMTY_SEQ_NO") or "").strip()
        if not title or not seq:
            continue

        posted_date = _normalize_date(item.get("ATON") or "")
        detail_url = f"{HOST}{DETAIL_PATH}?seqNo={seq}"

        detail_html, body_text = _fetch_body(s, seq, HOST + INQ_PATH)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRCD0305] 핸들러 추출 {len(results)}건")
    return results


register("KRCD0305", handle)

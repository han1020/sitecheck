"""
KRCD0311 - 롯데카드 (Lotte Card, corp 법인채널)

흐름:
  1) GET  /app/LCCSTAA_V100.lc (공지 페이지 → 세션 쿠키)
  2) GET  /app/LCCSTAA_A100.lc (svcf_Ajax 목록 호출)
     - 응답 JSON: {"Status":{"code":0,...}, "noticeList":[{...}]}
     - 각 항목이 본문 HTML(newsCn)까지 모두 포함 → 별도 상세 요청 불필요
       - newsSeq:    상세 식별자
       - newsTitNm:  제목
       - newsWrtDt:  등록일 (YYYYMMDD)
       - newsCn:     본문 HTML (점검 일시/업무/사유 파싱용)
  3) 상세 URL(참조용): /app/LCCSTAA_V200.lc?newsSeq={seq}
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

HOST = "https://corp.lottecard.co.kr"
LIST_PAGE_PATH = "/app/LCCSTAA_V100.lc"
LIST_AJAX_PATH = "/app/LCCSTAA_A100.lc"
DETAIL_PATH = "/app/LCCSTAA_V200.lc"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _normalize_date(s: str) -> str:
    """'20260618' / '2026.06.18' → '20260618'."""
    digits = re.sub(r"\D", "", s or "")
    return digits[:8] if len(digits) >= 8 else digits


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 공지 페이지 GET → 세션 쿠키 확보
    try:
        s.get(HOST + LIST_PAGE_PATH, timeout=20)
    except Exception as e:
        logger.debug(f"[KRCD0311] 초기 GET 실패(무시): {e}")

    # 2) 목록 AJAX GET → JSON.noticeList
    try:
        r = s.get(
            HOST + LIST_AJAX_PATH,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": HOST + LIST_PAGE_PATH,
            },
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRCD0311] 목록 요청 실패: {e}")
        return []

    raw = r.content.decode("utf-8", errors="replace")
    brace = raw.find("{")
    if brace < 0:
        logger.warning("[KRCD0311] 목록 응답에 JSON 없음")
        return []
    try:
        payload = json.loads(raw[brace:])
    except Exception as e:
        logger.warning(f"[KRCD0311] 목록 JSON 파싱 실패: {e}")
        return []

    notice_list = payload.get("noticeList") or []
    if not notice_list:
        logger.info("[KRCD0311] 목록 항목 없음")
        return []

    results: List[HandlerResult] = []

    for item in notice_list:
        if len(results) >= max_items:
            break

        title = (item.get("newsTitNm") or "").strip()
        if not title:
            continue

        seq = str(item.get("newsSeq") or "").strip()
        posted_date = _normalize_date(item.get("newsWrtDt") or "")

        detail_url = f"{HOST}{DETAIL_PATH}?newsSeq={seq}" if seq else ""

        # 본문(newsCn)이 목록 응답에 포함됨 → 상세 요청 불필요
        news_cn = item.get("newsCn") or ""
        detail_html = ""
        body_text = ""
        if news_cn:
            d_soup = BeautifulSoup(news_cn, "lxml")
            for tag in d_soup(["script", "style"]):
                tag.decompose()
            detail_html = str(d_soup).strip()
            body_text = d_soup.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRCD0311] 핸들러 추출 {len(results)}건")
    return results


register("KRCD0311", handle)

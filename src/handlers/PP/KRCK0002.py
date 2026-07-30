"""
KRCK0002 - 인터넷 등기소 (공지사항)

iros.go.kr 는 WebSquare SPA 라 목록/본문이 정적 HTML 에 없지만, 게시판 JSON API 는
세션·토큰 없이 plain POST 로 호출된다 (홈택스와 달리 봇 탐지 없음 → Playwright 불필요).

흐름:
  1. POST /biz/Pm10P0BltbdPerMngCtrl/retrieveBltbdPerList.do
       {"websquare_param":{"board_type_id":"2","board_category":"전체","pageIndex":1,...}}
     → dataList[]: board_id, boardtitle(순수 제목; board_title 은 new 아이콘 <img> 포함),
       create_don(YYYY-MM-DD)
  2. 각 건 POST /biz/Pm10P0BltbdPerMngCtrl/retrieveBltbdPerDetl.do
       {"websquare_param":{"board_id":"...","board_type_id":"2"}}
     → dataDtelMap.board_desc 가 본문 HTML.
       본문은 '○ 중단일시 : 2026년 7월 22일(수) 21:00 ~ 7월 23일(목) 06:00' /
       '○ 중단대상 : ...' 라벨 형식 — 기존 _label_value·extract_window 로 파싱됨.

상세는 POST 로만 접근되고 상세 화면 딥링크(linkParam)는 직접 로드 시 빈 화면이라,
detail_url 은 공지사항 목록 화면 딥링크(참조용, 사람이 클릭해 확인)로 넣는다.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult, block_text

logger = logging.getLogger(__name__)

HOST = "https://www.iros.go.kr"
LIST_API = HOST + "/biz/Pm10P0BltbdPerMngCtrl/retrieveBltbdPerList.do"
DETAIL_API = HOST + "/biz/Pm10P0BltbdPerMngCtrl/retrieveBltbdPerDetl.do"
NOTICE_LIST_URL = HOST + "/index.jsp?w2xPath=/ui/pm10/p0/regtinfo/Pm10P0NoticList.xml"
BOARD_TYPE_ID = "2"   # 공지사항 게시판
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")


def _post(s: requests.Session, url: str, param: dict) -> dict:
    r = s.post(url, json={"websquare_param": param}, timeout=20)
    r.raise_for_status()
    return r.json()


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 10) or 10
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": 'application/json; charset="UTF-8"',
        "Accept": "application/json, text/plain, */*",
        "Referer": HOST + "/index.jsp",
    })
    try:
        data = _post(s, LIST_API, {
            "board_type_id": BOARD_TYPE_ID, "swrd": "", "srch_cls_cd": "",
            "board_category": "전체", "pageIndex": 1, "top10_category_yn": "",
        })
        rows = data.get("dataList") or []
    except Exception as e:
        logger.warning(f"[KRCK0002] 목록 호출 실패: {e}")
        return []

    results: List[HandlerResult] = []
    for row in rows[:max_items]:
        board_id = row.get("board_id")
        # boardtitle 이 순수 제목이지만 <b> 등 태그가 섞인 건도 있어 태그 제거
        title = _TAG_RE.sub(
            "", row.get("boardtitle") or row.get("board_title") or "").strip()
        posted_date = (row.get("create_don") or "").strip()
        if not board_id or not title:
            continue
        try:
            detl = _post(s, DETAIL_API, {
                "board_id": str(board_id), "board_type_id": BOARD_TYPE_ID,
            })
            desc = (detl.get("dataDtelMap") or {}).get("board_desc") or ""
        except Exception as e:
            logger.debug(f"[KRCK0002] 상세 실패(board_id={board_id}): {e}")
            continue

        soup = BeautifulSoup(desc, "lxml")
        body_text = block_text(soup)
        detail_html = (
            f"<div><h3>{htmllib.escape(title)}</h3>"
            f"<p>등록일: {htmllib.escape(posted_date)}</p>"
            f"<div style=\"white-space:pre-line\">{desc}</div></div>"
        )
        results.append(HandlerResult(
            title=title, posted_date=posted_date,
            detail_url=NOTICE_LIST_URL,
            detail_html=detail_html, body_text=body_text,
        ))

    logger.info(f"[KRCK0002] 핸들러 추출 {len(results)}건")
    return results


register("KRCK0002", handle)

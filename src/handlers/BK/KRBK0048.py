"""
KRBK0048 - 신협 (기업뱅킹 공지사항)

흐름 (JSON POST API, 원본 스크래퍼 그대로 포팅):
  1. GET  https://bizbank.cu.co.kr/                           → 쿠키
  2. POST /corpIBPT/EI_CSC010201_01_ACT01.jct  (JSON)         → 공지 목록(REC)
       Body: {"ANNC_TY":"01","INQ_CHAR":"","PAGE_NUM":1,"PAGE_SIZE":10}
  3. POST /corpIBPT/EI_CSC010202_01_ACT01.jct  {"ANNC_IDX":no} → 상세(REC[0])
       상세 본문은 첨부 '이미지 파일'(jpg/png) → contentView img 태그로 구성

주의: 신협 공지 본문은 텍스트가 아니라 이미지(jpg/png) 한 장입니다.
      따라서 detail_html(스크린샷)에는 이미지가 들어가지만, body_text(점검 일시
      파싱용 텍스트)는 비어 있습니다. 제목에 날짜가 없는 점검 공지는 일시 파싱이
      불가능해 수집되지 않습니다. (원본 스크래퍼도 이미지만 저장)
"""
from __future__ import annotations

import json
import logging
from typing import List

import requests

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://bizbank.cu.co.kr"
LIST_PATH = "/corpIBPT/EI_CSC010201_01_ACT01.jct"
DETAIL_PATH = "/corpIBPT/EI_CSC010202_01_ACT01.jct"
CONTENT_VIEW = HOST + "/corpIBPT/contentView"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif")


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })
    return s


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    try:
        s.get(HOST + "/", timeout=20)
        s.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        })
        r = s.post(
            HOST + LIST_PATH,
            data=json.dumps({
                "ANNC_TY": "01", "INQ_CHAR": "", "PAGE_NUM": 1, "PAGE_SIZE": 10,
            }),
            timeout=20,
        )
        r.raise_for_status()
        rec_list = r.json().get("REC") or []
    except Exception as e:
        logger.warning(f"[KRBK0048] 목록 조회 실패: {e}")
        return []

    results: List[HandlerResult] = []

    for ele in rec_list:
        notice_no = str(ele.get("ANNC_IDX") or "").strip()
        title = str(ele.get("ANNC_TIT") or "").strip()
        if not notice_no or not title:
            continue
        posted_date = str(ele.get("REG_DTIM") or "").strip()[:8]

        try:
            dr = s.post(
                HOST + DETAIL_PATH,
                data=json.dumps({"ANNC_IDX": notice_no}),
                timeout=20,
            )
            dr.raise_for_status()
            detail = (dr.json().get("REC") or [{}])[0]
        except Exception as e:
            logger.debug(f"[KRBK0048] 상세 조회 실패(idx={notice_no}): {e}")
            continue

        ext = str(detail.get("FILE_EXNS_NM") or "").lower()
        if ext not in _IMG_EXTS:
            # 이미지가 아닌 첨부(예: 한글/pdf)는 본문 표현 불가 → 스킵
            logger.debug(f"[KRBK0048] 이미지 본문 아님(ext={ext}), skip: {title}")
            continue

        img_url = f"{CONTENT_VIEW}?no={detail.get('FILE_NO')}&seq={detail.get('FILE_SEQ')}"
        detail_html = f'<img alt="신협" src="{img_url}">'

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=img_url,
            detail_html=detail_html,
            body_text="",  # 본문이 이미지뿐 → 텍스트 없음
        ))

    logger.info(f"[KRBK0048] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0048", handle)

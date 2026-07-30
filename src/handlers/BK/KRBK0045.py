"""
KRBK0045 - 새마을금고 (기업 인터넷뱅킹 공지사항)

흐름 (ib20 프레임워크, form-token POST, 원본 스크래퍼 그대로 포팅):
  1. GET  https://biz.kfcc.co.kr/                              → 쿠키
  2. GET  /ib20/mnu/CIB000000000130                            → 목록 HTML + #detailForm 토큰
  3. #detailForm input 에서 ib20_action / ib20_cur_mnu / ib20_cur_wgt 추출
  4. table.trigger-con tbody tr 순회 (헤더: 번호/제목/등록일/조회)
       a href = noticeView('NOTICEID')  에서 NOTICEID 추출
  5. POST {action}?ib20_cur_mnu=...&ib20_cur_wgt=...  (form, BOARDID/NOW_PAGE/NOTICEID 포함)
       → 상세 HTML (div.noticeWrap, 없으면 #wrap)
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://biz.kfcc.co.kr"
LIST_PATH = "/ib20/mnu/CIB000000000130"
BOARDID = "00000011"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
)
_NOTICEID_RE = re.compile(r"noticeView\('([^']+)'\)")


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


def handle(site_config) -> List[HandlerResult]:
    s = _new_session()

    try:
        s.get(HOST + "/", timeout=20)
        r = s.get(HOST + LIST_PATH, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRBK0045] 목록 페이지 호출 실패: {e}")
        return []

    soup = BeautifulSoup(r.text, "lxml")

    form = soup.select_one("#detailForm")
    inp_data = {}
    if form:
        for inp in form.select("input"):
            inp_data[inp.get("name")] = inp.get("value") or ""

    action = inp_data.get("ib20_action")
    cur_mnu = inp_data.get("ib20_cur_mnu")
    cur_wgt = inp_data.get("ib20_cur_wgt")
    if not (action and cur_mnu and cur_wgt):
        logger.warning("[KRBK0045] detailForm 토큰 추출 실패")
        return []

    inp_data["BOARDID"] = BOARDID
    inp_data["NOW_PAGE"] = "1"
    detail_url = f"{HOST}{action}?ib20_cur_mnu={cur_mnu}&ib20_cur_wgt={cur_wgt}"

    headers = [th.get_text(strip=True) for th in soup.select("table.trigger-con thead th")]
    try:
        title_idx = headers.index("제목")
        date_idx = headers.index("등록일")
    except ValueError:
        title_idx, date_idx = 1, 2

    results: List[HandlerResult] = []

    for tr in soup.select("table.trigger-con tbody tr"):
        tds = tr.select("td")
        if len(tds) <= max(title_idx, date_idx):
            continue
        title = re.sub(r"\s+", " ", tds[title_idx].get_text(" ", strip=True)).strip()
        if not title:
            continue
        posted_date = re.sub(r"[.\-]", "", tds[date_idx].get_text(strip=True)).strip()

        a = tr.select_one("a")
        m = _NOTICEID_RE.search(a.get("href", "")) if a else None
        if not m:
            continue
        notice_id = m.group(1).strip()

        post_data = dict(inp_data)
        post_data["NOTICEID"] = notice_id

        try:
            dr = s.post(detail_url, data=post_data, timeout=30)
            dr.raise_for_status()
        except Exception as e:
            logger.debug(f"[KRBK0045] 상세 조회 실패(NOTICEID={notice_id}): {e}")
            continue

        dsoup = BeautifulSoup(dr.text, "lxml")
        for st in dsoup.find_all("style"):
            st.decompose()
        node = dsoup.select_one("div.noticeWrap") or dsoup.select_one("#wrap")
        if not node:
            continue
        detail_html = str(node).strip()
        body_text = node.get_text("\n", strip=True)

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRBK0045] 핸들러 추출 {len(results)}건")
    return results


register("KRBK0045", handle)

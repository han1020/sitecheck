"""
KRST0280 - 유진투자증권 (Eugene Investment & Securities)

흐름:
  1) POST https://www.eugenefn.com/comm/msgList.do
       form body:
         menu_id=03060600
         &menu_url=/serv/svsc/svsc600r
         &menu_level=3
         &sDiv=1
         &board_yn=Y
         &ch_munu_id=undefined
         &regId=
         &goMenu=
         &sAccountNumber=
         &req_type=2
     응답 HTML 에서:
       - 목록 테이블(div.tbl_vType table) thead/tbody 파싱
       - #listForm 의 hidden input 들 (oDay, dDay, mDay, qDay, hDay, yDay) 수집
       - 행의 <a href="javascript:searchDetail('NOTICEID');"> 에서 msgId 추출
       - '번호' 칸이 '공지' 인 행은 스킵
  2) POST https://www.eugenefn.com/comm/msgDetail.do
       form body: (위 hidden 값 + msgId + 추가 파라미터)
     응답 HTML 의 table tbody tr td.view_cont 영역을 detail_html / body_text 로 사용
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

HOST = "https://www.eugenefn.com"
LIST_PATH = "/comm/msgList.do"
DETAIL_PATH = "/comm/msgDetail.do"
REFERER = HOST + "/comm/msgList.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

_SEARCH_DETAIL_RE = re.compile(r"searchDetail\('([^']+)'")
_DATE_NORM_RE = re.compile(r"\D")


def _normalize_date(s: str) -> str:
    return _DATE_NORM_RE.sub("", s or "")


def _extract_msg_id(a_tag) -> str:
    if a_tag is None:
        return ""
    for attr in ("href", "onclick"):
        val = a_tag.get(attr) or ""
        m = _SEARCH_DETAIL_RE.search(val)
        if m:
            return m.group(1).strip()
    return ""


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.7"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded",
        "Connection": "keep-alive",
    })

    # 1) 목록 POST (page 1)
    list_body = (
        "menu_id=03060600"
        "&menu_url=/serv/svsc/svsc600r"
        "&menu_level=3"
        "&sDiv=1"
        "&board_yn=Y"
        "&ch_munu_id=undefined"
        "&regId="
        "&goMenu="
        "&sAccountNumber="
        "&req_type=2"
    )

    list_headers = {
        "Referer": REFERER,
        "Origin": HOST,
    }

    try:
        r = s.post(
            HOST + LIST_PATH,
            data=list_body,
            headers=list_headers,
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0280] 목록 요청 실패: {e}")
        return []

    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")

    # listForm 의 hidden input → 상세 POST 파라미터로 사용
    inp_data = {}
    list_form = soup.find(id="listForm")
    if list_form is not None:
        for inp in list_form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            inp_data[name] = inp.get("value") or ""

    required_keys = ("oDay", "dDay", "mDay", "qDay", "hDay", "yDay")
    if not all(inp_data.get(k) for k in required_keys):
        logger.warning(
            f"[KRST0280] listForm hidden input 부족: "
            f"{ {k: inp_data.get(k) for k in required_keys} }"
        )
        # hidden 값이 없으면 상세 호출 자체가 불가 → 목록만으로 진행
        # 그러나 여기서는 안전하게 빈 결과 반환
        return []

    table = soup.select_one("div.tbl_vType table")
    if table is None:
        logger.warning("[KRST0280] 목록 테이블(div.tbl_vType table) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0280] thead 컬럼 없음")
        return []

    def col_idx(name: str) -> int:
        try:
            return thead_names.index(name)
        except ValueError:
            return -1

    idx_no = col_idx("번호")
    idx_title = col_idx("제목")
    idx_date = col_idx("작성일")

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0280] tbody tr 없음")
        return []

    detail_headers = {
        "Referer": REFERER,
        "Origin": HOST,
    }

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        def cell_text(i: int) -> str:
            if i < 0 or i >= len(tds):
                return ""
            return tds[i].get_text(strip=True)

        notice_no = cell_text(idx_no)
        if notice_no == "공지":
            continue

        title = ""
        msg_id = ""
        if 0 <= idx_title < len(tds):
            a = tds[idx_title].find("a")
            if a is not None:
                title = re.sub(r"\s+", " ", a.get_text(strip=True)).strip()
                msg_id = _extract_msg_id(a)
            if not title:
                title = re.sub(r"\s+", " ", tds[idx_title].get_text(strip=True)).strip()

        posted_date = _normalize_date(cell_text(idx_date))

        if not title or not msg_id:
            continue

        detail_body = (
            "menu_id=03060600"
            "&menu_url=/serv/svsc/svsc600r"
            "&menu_level=3"
            f"&oDay={inp_data.get('oDay', '')}"
            f"&dDay={inp_data.get('dDay', '')}"
            f"&mDay={inp_data.get('mDay', '')}"
            f"&qDay={inp_data.get('qDay', '')}"
            f"&hDay={inp_data.get('hDay', '')}"
            f"&yDay={inp_data.get('yDay', '')}"
            "&sDiv=1"
            "&boardUrl=/serv/svsc/svsc601r"
            f"&msgId={msg_id}"
            "&divfnd="
            "&numDate=7"
            "&pageNo=1"
            "&searchKey=1"
            "&keyWord="
            "&startDay="
            "&endDay="
            "&pageNo=1"
        )

        detail_html = ""
        body_text = ""
        try:
            dr = s.post(
                HOST + DETAIL_PATH,
                data=detail_body,
                headers=detail_headers,
                timeout=15,
            )
            dr.raise_for_status()

            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")
            for t in d_soup.find_all("script"):
                t.decompose()

            view = d_soup.select_one("table tbody tr td.view_cont")
            if view is not None:
                detail_html = view.decode_contents().strip()
                body_text = view.get_text("\n", strip=True)
            else:
                detail_html = dt_text
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.debug(f"[KRST0280] 상세 요청 실패({title[:30]}): {e}")

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=f"{HOST}{DETAIL_PATH}?msgId={msg_id}",
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0280] 핸들러 추출 {len(results)}건")
    return results


register("KRST0280", handle)

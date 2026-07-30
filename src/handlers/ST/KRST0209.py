"""
KRST0209 - 유안타증권 (Yuanta Securities Korea)

흐름:
  1) GET https://www.myasset.com (초기 쿠키)
  2) GET https://www.myasset.com/myasset/customer/notice/CU_0201000_P1.cmd?gubun=norNotice
     목록 HTML 파싱 (div.rsReportTbl table)
       - thead th 로 컬럼 순서 결정 ('번호', '제목', '작성일', '조회수')
       - 제목 칸의 <a data-seq="..."> 에서 SEQ 추출
       - 작성일 셀 텍스트에서 공백/./-// 제거 → YYYYMMDD
  3) 상세: GET /myasset/customer/notice/CU_0201000_P2.cmd?gubun=norNotice&SEQ={seq}
       - script/quick/footer/lnb 등 불필요 영역 제거 후
       - div.listColAllWrap 영역을 detail_html / body_text 로 사용
"""
from __future__ import annotations

import logging
import re
from typing import List

import requests
from bs4 import BeautifulSoup, Comment

from .. import register
from ..base import HandlerResult

logger = logging.getLogger(__name__)

HOST = "https://www.myasset.com"
LIST_PATH = "/myasset/customer/notice/CU_0201000_P1.cmd?gubun=norNotice"
DETAIL_PATH = "/myasset/customer/notice/CU_0201000_P2.cmd?gubun=norNotice&SEQ="

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Whale/3.18.154.13 Safari/537.36"
)

# 상세 페이지에서 제거할 요소 셀렉터 (JS 원본의 $dt(...).remove() 목록)
_DETAIL_STRIP_SELECTORS = [
    "script",
    "#quick",
    "#snsShareQ",
    "#frmCnd",
    "div#footer",
    "#accNav",
    "#gnb",
    "div.lineMapWrap",
    "#lnb",
    "div.pageOptWrap",
    "div.boardPager",
]

_DATE_CLEAN_RE = re.compile(r"[\s./\-]")


def _normalize_date(s: str) -> str:
    """'2025.05.29' / '2025-05-29' / '2025/05/29' 등에서 숫자만 추출(YYYYMMDD)."""
    if not s:
        return ""
    return _DATE_CLEAN_RE.sub("", s).strip()


def _index_or(names: List[str], target: str, default: int) -> int:
    try:
        return names.index(target)
    except ValueError:
        return default


def handle(site_config) -> List[HandlerResult]:
    max_items = getattr(site_config, "max_items", 15)

    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,"
            "application/signed-exchange;v=b3;q=0.9"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # 1) 초기 쿠키
    try:
        s.get(HOST, timeout=15)
    except Exception as e:
        logger.debug(f"[KRST0209] 초기 GET 실패(무시): {e}")

    # 2) 목록 GET
    list_url = HOST + LIST_PATH
    try:
        r = s.get(list_url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"[KRST0209] 목록 요청 실패: {e}")
        return []

    try:
        r.encoding = r.apparent_encoding or "utf-8"
        list_html = r.text
    except Exception:
        list_html = r.content.decode("utf-8", errors="replace")

    soup = BeautifulSoup(list_html, "lxml")

    table = soup.select_one("div.rsReportTbl table")
    if table is None:
        logger.warning("[KRST0209] 목록 테이블(div.rsReportTbl table) 없음")
        return []

    thead_names = [th.get_text(strip=True) for th in table.select("thead th")]
    if not thead_names:
        logger.warning("[KRST0209] thead 컬럼 없음")
        return []

    idx_no = _index_or(thead_names, "번호", 0)
    idx_title = _index_or(thead_names, "제목", 1)
    idx_date = _index_or(thead_names, "작성일", 2)

    rows = table.select("tbody tr")
    if not rows:
        logger.info("[KRST0209] tbody tr 없음 (공지 없음)")
        return []

    results: List[HandlerResult] = []

    for tr in rows:
        if len(results) >= max_items:
            break

        tds = tr.find_all("td", recursive=False)
        if not tds:
            continue

        def cell(i: int):
            if 0 <= i < len(tds):
                return tds[i]
            return None

        # 제목 + SEQ
        title = ""
        seq = ""
        title_cell = cell(idx_title)
        if title_cell is not None:
            a = title_cell.find("a")
            if a is not None:
                title = a.get_text(strip=True)
                seq = (a.get("data-seq") or "").strip()
            if not title:
                title = title_cell.get_text(strip=True)

        if not title or not seq:
            continue

        # 작성일 → YYYYMMDD
        date_cell = cell(idx_date)
        posted_date = _normalize_date(date_cell.get_text() if date_cell else "")
        if not (posted_date.isdigit() and len(posted_date) == 8):
            # 날짜 비정상이면 스킵 (JS 원본도 err_no:2701 후 continue)
            logger.debug(
                f"[KRST0209] 작성일 형식 비정상 → 스킵: title={title[:40]} raw={date_cell.get_text(strip=True) if date_cell else ''!r}"
            )
            continue

        # (참고) 번호: idx_no
        _ = cell(idx_no)

        detail_url = HOST + DETAIL_PATH + seq

        detail_html = ""
        body_text = ""
        try:
            dr = s.get(detail_url, timeout=20)
            dr.raise_for_status()
            try:
                dr.encoding = dr.apparent_encoding or "utf-8"
                dt_text = dr.text
            except Exception:
                dt_text = dr.content.decode("utf-8", errors="replace")

            d_soup = BeautifulSoup(dt_text, "lxml")

            # 불필요 요소 제거 (JS 원본과 동일)
            for sel in _DETAIL_STRIP_SELECTORS:
                for t in d_soup.select(sel):
                    t.decompose()

            # 주석 제거
            for c in d_soup.find_all(string=lambda x: isinstance(x, Comment)):
                c.extract()

            # link[rel=stylesheet] 중 href에 'common'이 들어가지 않은 것은 유지, 그 외 <link>는 제거
            for link in d_soup.find_all("link"):
                rel = link.get("rel") or []
                if isinstance(rel, list):
                    rel_val = " ".join(rel).lower()
                else:
                    rel_val = str(rel).lower()
                href = (link.get("href") or "")
                if rel_val == "stylesheet":
                    if href and "common" in href:
                        link.decompose()
                else:
                    link.decompose()

            content = d_soup.select_one("div.listColAllWrap")
            if content is not None:
                detail_html = str(content).strip()
                # body_text: 본문 텍스트 + 이미지 src 나열 (JS 원본 동작 유사)
                text_part = content.get_text("\n", strip=True)
                # 공백/줄바꿈 정리
                text_part = re.sub(r"[ \t]+", " ", text_part)
                text_part = re.sub(r"\n{2,}", "\n", text_part).strip()

                img_parts: List[str] = []
                for img in content.find_all("img"):
                    src = img.get("src") or ""
                    if not src:
                        continue
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = HOST + src
                    img_parts.append(f'<img src="{src}">')
                if img_parts:
                    body_text = (text_part + "\n" + "".join(img_parts)).strip()
                else:
                    body_text = text_part
            else:
                # fallback: 본문 컨테이너를 못 찾으면 전체 텍스트 사용
                detail_html = str(d_soup)
                body_text = d_soup.get_text("\n", strip=True)
        except Exception as e:
            logger.warning(f"[KRST0209] 상세 요청 실패({title[:30]}): {e}")
            # 본문 못 가져왔어도 목록 정보만으로 한 건 추가 (이후 단계에서 처리)
            # JS 원본은 detail HTML/Text 없으면 skip 하지만, Python 측은 기존 다른
            # 핸들러(KRST0218 등)들과 동일하게 비어 있는 채로도 push 한다.
            pass

        results.append(HandlerResult(
            title=title,
            posted_date=posted_date,
            detail_url=detail_url,
            detail_html=detail_html,
            body_text=body_text,
        ))

    logger.info(f"[KRST0209] 핸들러 추출 {len(results)}건")
    return results


register("KRST0209", handle)

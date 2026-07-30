"""핸들러 공통 데이터 구조 + 유틸."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class HandlerResult:
    """사이트별 핸들러가 반환하는 공지 1건."""
    title: str
    posted_date: str       # 'YYYYMMDD' 또는 'YYYY-MM-DD' 등 사이트별 원문
    detail_url: str        # 참조용 (엑셀 링크). POST로만 접근 가능하면 빈 문자열
    detail_html: str       # 상세 페이지 HTML (스크린샷용 set_content 입력)
    body_text: str         # 본문 plain text (일시/업무/사유 파싱용)


def block_text(root) -> str:
    """블록 요소(p/li/td 등) 단위로 줄을 나눈 본문 텍스트 추출.

    get_text("\\n")은 인라인 태그마다 줄을 쪼개서, 워드 붙여넣기 공지처럼
    단어마다 <span>인 본문(예: SK증권)이 단어별 줄바꿈이 된다. 그러면
    _label_value가 값 중간의 단독 줄('서비스')을 라벨로 오인할 수 있다.
    여기서는 블록 요소 안의 인라인 텍스트를 그대로 이어붙이고(공백 span 보존),
    블록 경계와 <br>에서만 줄을 나눈다.
    """
    if root is None:
        return ""
    for br in root.find_all("br"):
        br.replace_with("\n")
    blocks = root.find_all(["p", "li", "td", "th", "h1", "h2", "h3", "h4"])
    if not blocks:
        text = root.get_text("", strip=False)
    else:
        lines = []
        for b in blocks:
            # 중첩 블록(td 안의 p 등)은 하위 블록 쪽에서 처리하므로 건너뜀
            if b.find(["p", "li", "td", "th"]):
                continue
            lines.append(b.get_text("", strip=False))
        text = "\n".join(lines)
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"[ \t\xa0​]+", " ", ln).strip()
        if ln:
            out.append(ln)
    return "\n".join(out)

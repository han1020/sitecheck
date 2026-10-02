"""pytest 공용 fixture.

테스트용 워크북은 실제 excel_writer.write_excel 로 만들어 '점검' 시트 레이아웃
(헤더 2행, B:G 6열, 신규=노란색)이 운영 파일과 항상 같도록 한다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

import pytest

from src.carryover import CarryoverEntry
from src.config_loader import RegularEntry
from src.datetime_parser import extract_window
from src.excel_writer import write_excel
from src.scraper import NoticeHit

# 6칸 순서: 구분, 기관코드, 기관명, 일시, 업무, 사유  (엑셀 B:G 와 동일)
Cells = Sequence[str]


def _hit(cells: Cells) -> NoticeHit:
    """노란색(신규) 행 → 스크래퍼가 만든 NoticeHit 흉내."""
    _kind, code, name, sched, svc, reason = cells
    return NoticeHit(
        site_code=code, site_name=name, category="BK",
        title=f"{name} 시스템 점검 안내", posted_date="2026-09-01",
        detail_url="", screenshot_path="",
        window=extract_window(sched), schedule_text=sched,
        service_text=svc, reason_text=reason,
    )


@pytest.fixture
def make_xlsx(tmp_path: Path) -> Callable[..., Path]:
    """make_xlsx(new=[6칸...], carried=[6칸...], regular=[6칸...]) -> 워크북 경로.

    new     : 이번 수집 신규 행 (노란색)
    carried : 이전 엑셀에서 넘어온 일반점검 행 (흰색) — 담당자 수정값 시나리오용
    regular : 정기점검 baseline 행 (흰색, 일시는 반복 문구)
    행 순서는 write_excel 정렬 규칙을 따르므로 테스트는 위치가 아니라 내용으로 행을 찾는다.
    """
    def _make(new: Sequence[Cells] = (), carried: Sequence[Cells] = (),
              regular: Sequence[Cells] = (),
              name: str = "[사이트점검]_20260923.xlsx") -> Path:
        hits = [_hit(c) for c in new]
        carry = [CarryoverEntry(*c) for c in carried]
        reg = [RegularEntry(code=c[1], name=c[2], schedule=c[3], service=c[4], reason=c[5])
               for c in regular]
        out = tmp_path / name
        write_excel(hits, reg, out, run_id="test", carried=carry)
        return out
    return _make

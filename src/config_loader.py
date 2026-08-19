from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


@dataclass
class SiteConfig:
    code: str
    name: str
    category: str
    list_url: str
    enabled: bool = False
    list_item_selector: str = "table tbody tr"
    title_selector: str = "td.title a, td a"
    date_selector: str = ""
    detail_url_attr: str = "href"
    detail_content_selector: str = "body"
    wait_selector: str = ""
    max_items: int = 20
    # 자기 기관 검증 시 추가로 인정할 별칭(예: 통합 사이트의 하위 기관명)
    aliases: List[str] = field(default_factory=list)


@dataclass
class KeywordConfig:
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    # 제목에서만 매칭하는 제외 키워드. 업무/사유 라벨 '값'에 정상적으로 등장할 수
    # 있는 단어(예: 'CD공동망')를 exclude에 두면 공지 전체가 잘못 빠진다.
    exclude_title: List[str] = field(default_factory=list)
    # 제목/라벨이 아니라 본문 전체에서 매칭하는 제외 키워드.
    # (오탐 위험이 있으므로 '투자정보 콘텐츠'처럼 충분히 특이한 문구만 넣을 것)
    exclude_body: List[str] = field(default_factory=list)
    # 외부기관 자동 감지용 명부 보강. sites.yaml 의 기관명과 합쳐 쓴다.
    external_orgs: List[str] = field(default_factory=list)
    institution_aliases: List[str] = field(default_factory=list)
    # 외부기관 작업 안내라도 버리지 않고 '검토 필요'로 보낼 기관 (예: 금융결제원, 코스콤 —
    # 인증서 등 자사 서비스 영향 가능성이 있어 사람이 확인)
    external_review: List[str] = field(default_factory=list)
    # 업무 라벨 값에 있으면 제외 검사 없이 무조건 점검 공지로 판단 (예: 인터넷뱅킹)
    force_include_service: List[str] = field(default_factory=list)
    # 제목에 이 마커가 있으면 어떤 필터에 걸려도 버리지 않고 '검토 필요'로 보낸다 (예: [중요])
    force_review_title: List[str] = field(default_factory=list)


@dataclass
class RegularEntry:
    """정기점검 baseline 한 줄."""
    code: str
    name: str
    schedule: str
    service: str = ""
    reason: str = ""


def load_sites(path: Path) -> List[SiteConfig]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [SiteConfig(**item) for item in data.get("sites", [])]


def load_keywords(path: Path) -> KeywordConfig:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return KeywordConfig(
        include=[k.strip() for k in data.get("include", []) if k.strip()],
        exclude=[k.strip() for k in data.get("exclude", []) if k.strip()],
        exclude_title=[k.strip() for k in data.get("exclude_title", []) if k.strip()],
        exclude_body=[k.strip() for k in data.get("exclude_body", []) if k.strip()],
        external_orgs=[k.strip() for k in data.get("external_orgs", []) if k.strip()],
        institution_aliases=[
            k.strip() for k in data.get("institution_aliases", []) if k.strip()
        ],
        external_review=[k.strip() for k in data.get("external_review", []) if k.strip()],
        force_include_service=[
            k.strip() for k in data.get("force_include_service", []) if k.strip()
        ],
        force_review_title=[
            k.strip() for k in data.get("force_review_title", []) if k.strip()
        ],
    )


def load_regular(path: Path) -> List[RegularEntry]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [RegularEntry(**item) for item in data.get("regular", [])]

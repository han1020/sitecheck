"""
사이트별 커스텀 핸들러.

기본 config-driven Playwright 방식으로 처리 불가능한 사이트(예: POST API + 토큰
인증이 필요한 금융권 사이트)를 위한 사이트별 핸들러를 모듈로 등록합니다.

각 핸들러는 `handle(site_config) -> list[HandlerResult]` 함수만 제공하면 됩니다.
"""
from __future__ import annotations

from typing import Callable, Dict

from .base import HandlerResult

# 동적 import로 등록 (핸들러 추가될 때만 import)
_REGISTRY: Dict[str, Callable] = {}


def register(code: str, handler_fn: Callable) -> None:
    _REGISTRY[code] = handler_fn


def get_handler(code: str):
    return _REGISTRY.get(code)


def has_handler(code: str) -> bool:
    return code in _REGISTRY


# 카테고리 서브패키지 import (각 모듈이 import 시점에 register() 호출)
from . import BK  # noqa: E402,F401  은행
from . import CD  # noqa: E402,F401  카드
from . import ST  # noqa: E402,F401  증권
from . import PP  # noqa: E402,F401  공공


__all__ = ["HandlerResult", "register", "get_handler", "has_handler"]

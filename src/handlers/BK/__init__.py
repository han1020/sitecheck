"""은행(BK) 카테고리 핸들러 모음.

각 모듈은 import 시점에 ``register(...)`` 를 호출해 자동 등록됨.
새 은행 핸들러를 추가하려면 이 폴더에 KRBK*.py 파일을 두고 아래 목록에 추가.
"""
from . import KRBK0002  # noqa: F401 산업은행
from . import KRBK0003  # noqa: F401 기업은행
from . import KRBK0004  # noqa: F401 국민은행
from . import KRBK0007  # noqa: F401 수협은행
from . import KRBK0011  # noqa: F401 농협은행
from . import KRBK0020  # noqa: F401 우리은행
from . import KRBK0023  # noqa: F401 SC은행
from . import KRBK0027  # noqa: F401 씨티은행
from . import KRBK0031  # noqa: F401 대구은행
from . import KRBK0032  # noqa: F401 부산은행
from . import KRBK0034  # noqa: F401 광주은행
from . import KRBK0035  # noqa: F401 제주은행
from . import KRBK0037  # noqa: F401 전북은행
from . import KRBK0039  # noqa: F401 경남은행
from . import KRBK0045  # noqa: F401 새마을금고
from . import KRBK0048  # noqa: F401 신협
from . import KRBK0071  # noqa: F401 우체국
from . import KRBK0081  # noqa: F401 KEB하나은행
from . import KRBK0088  # noqa: F401 신한은행
from . import KRBK0089  # noqa: F401 K뱅크
# --- 저축은행 ---
from . import KRBK0101  # noqa: F401 저축은행중앙회(통합)
from . import KRBK0102  # noqa: F401 대신저축은행
from . import KRBK0103  # noqa: F401 SBI저축은행
from . import KRBK0104  # noqa: F401 애큐온저축은행
from . import KRBK0105  # noqa: F401 웰컴저축은행
from . import KRBK0106  # noqa: F401 KB저축은행
from . import KRBK0107  # noqa: F401 푸른저축은행
from . import KRBK0108  # noqa: F401 하나저축은행
from . import KRBK0109  # noqa: F401 DB저축은행
from . import KRBK0110  # noqa: F401 NH저축은행
from . import KRBK0111  # noqa: F401 OSB저축은행
from . import KRBK0112  # noqa: F401 BNK저축은행
from . import KRBK0113  # noqa: F401 신한저축은행

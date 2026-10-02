from __future__ import annotations

import pytest

from src.notice_api import NoticeApi, NoticeApiError


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self.ok = status < 400
        self.payload = payload

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def test_lookup_and_candidate_pagination():
    session = Session([
        Response(200, {"data": {"results": [{"status": "NOT_FOUND", "item": None}]}}),
        Response(200, {"data": {"items": [{"itemId": "2"}], "hasNext": True,
                                 "nextBeforeId": "2"}}),
        Response(200, {"data": {"items": [{"itemId": "1"}], "hasNext": False,
                                 "nextBeforeId": None}}),
    ])
    api = NoticeApi("https://example.test", auth_header="X-API-Key",
                    auth_value="secret", session=session)
    assert api.lookup([{"clientRow": 1}])[0]["status"] == "NOT_FOUND"
    assert [v["itemId"] for v in api.candidates({"checkType": "일반점검",
        "institutionCode": "KRBK0101", "scheduleText": "2026.09.19"})] == ["2", "1"]
    assert session.calls[0][1].endswith("/api/v1/site-check-items:lookup")
    assert session.calls[0][2]["headers"]["X-API-Key"] == "secret"
    assert session.calls[2][2]["params"]["beforeId"] == "2"


def test_api_error_preserves_code_and_status():
    api = NoticeApi("https://example.test", session=Session([
        Response(409, {"error": {"code": "LINK_TARGET_CHANGED", "message": "changed"}})
    ]))
    with pytest.raises(NoticeApiError) as caught:
        api.create({"checkType": "일반점검"})
    assert caught.value.status == 409
    assert caught.value.code == "LINK_TARGET_CHANGED"


@pytest.mark.parametrize("status,code", [(401, "UNAUTHORIZED"), (403, "FORBIDDEN")])
def test_empty_auth_error_keeps_http_status(status, code):
    class EmptyResponse(Response):
        def json(self):
            raise ValueError("empty body")

    api = NoticeApi("https://example.test", session=Session([
        EmptyResponse(status, None)
    ]))
    with pytest.raises(NoticeApiError) as caught:
        api.lookup([])
    assert caught.value.status == status
    assert caught.value.code == code

"""The readiness checklist: one request shaped like a build's, judged without echoing it."""

from __future__ import annotations

import json

from boardmodeler import agent_providers
from boardmodeler.providers.http_inference import HttpResponse
from boardmodeler.security.readiness import local_checks, overall, verify_provider_tools

PROVIDER = agent_providers.by_id("opencode_go") or agent_providers.default_provider()
KEY = "sk-test-not-a-real-key-123"


def _reply(content: str, status: int = 200) -> HttpResponse:
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    return HttpResponse(status=status, headers={}, body=json.dumps(body).encode("utf-8"))


def _run(response: HttpResponse):
    seen = []

    def transport(request):
        seen.append(request)
        return response

    key, model = verify_provider_tools(PROVIDER, KEY, transport=transport)
    return key, model, seen


def test_a_files_reply_passes_both_lights_and_the_request_matches_a_build():
    key, model, seen = _run(_reply('{"files": {"check.txt": "OK"}}'))
    assert (key.state, model.state) == ("ok", "ok")
    body = json.loads(seen[0].body)
    assert seen[0].headers["User-Agent"].startswith("SpiceMaker/")
    if "reasoning_effort" in PROVIDER.extra_body:
        assert body["reasoning_effort"] == "low"
    assert KEY not in key.detail + model.detail


def test_prose_instead_of_the_files_json_fails_the_model_light():
    key, model, _ = _run(_reply("Sure! Here is your file: OK"))
    assert (key.state, model.state) == ("ok", "fail")
    assert "JSON file format" in model.detail


def test_a_rejected_key_is_red_and_the_model_is_not_claimed():
    key, model, _ = _run(_reply("", status=401))
    assert key.state == "fail" and model.state == "unchecked"


def test_quota_is_amber_not_red():
    key, _, _ = _run(_reply("", status=429))
    assert key.state == "warn"


def test_a_bad_model_id_is_the_model_lights_fault():
    key, model, _ = _run(_reply("", status=404))
    assert key.state == "ok" and model.state == "fail"


def test_local_checks_send_nothing_and_cover_every_light(monkeypatch):
    import boardmodeler.authoring.api_backend as api

    monkeypatch.setattr(
        api, "urllib_transport", lambda request: (_ for _ in ()).throw(AssertionError)
    )
    checks = local_checks()
    assert [check.key for check in checks] == ["key", "model", "ltspice", "pdf", "ocr", "internet"]
    assert overall(checks) in {"fail", "unchecked", "warn", "ok"}

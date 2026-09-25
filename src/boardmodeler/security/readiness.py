"""Is this copy ready to make a model? One checklist for the window's status lights.

Each :class:`Check` is one light: ``ok`` (green), ``warn`` (amber: usable, with a limit
worth knowing), ``fail`` (red: a build would stop here) or ``unchecked`` (grey: not
tested yet). :func:`local_checks` reads only this machine and sends nothing, so the
window can show it the moment it opens. :func:`verify_all` adds the two checks that
need the provider — the key is accepted, and the configured model answers in the JSON
``{"files": ...}`` format the generator parses — plus an LTspice smoke run.

The provider check is one small request in exactly the shape a build sends (same
endpoint, model and reasoning switch), so a pass means GO will not be refused for the
key, the model id or the reply format. No key value, request or response text is ever
put in a detail string.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CAPABILITY_PROMPT",
    "Check",
    "local_checks",
    "overall",
    "verify_all",
    "verify_provider_tools",
]

#: The whole request the model check sends: no datasheet, no model text.
CAPABILITY_PROMPT = (
    "Capability check for a SPICE model generator. Do not use tools or read files. "
    "Write one file named check.txt whose complete text is OK."
)

CHECK_TIMEOUT_S = 45.0

_ORDER = ("key", "model", "ltspice", "pdf", "ocr", "internet")


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    state: str  # "ok" | "warn" | "fail" | "unchecked"
    detail: str


def overall(checks: list[Check]) -> str:
    """The worst state among the checks a build needs (OCR only warns)."""
    states = {check.state for check in checks}
    for state in ("fail", "unchecked", "warn"):
        if state in states:
            return state
    return "ok"


def _provider_and_key(config):
    from boardmodeler.security.credentials import get_credential
    from boardmodeler.settings_summary import configured_provider

    provider, problem = configured_provider(config)
    if provider is None:
        return None, None, problem or "no agent provider is configured; open SETUP"
    try:
        # The same walk the backend performs (saved file, BOARDMODELER_*_API_KEY, then the
        # catalog's own aliases), so the light cannot disagree with GO.
        from boardmodeler.authoring.api_backend import credential_for

        credential = credential_for(provider)
    except ImportError:
        credential = get_credential(provider.credential)
    return provider, credential, ""


def _key_stored(config) -> Check:
    provider, credential, problem = _provider_and_key(config)
    if provider is None:
        return Check("key", "API KEY", "fail", problem)
    if credential is None or credential.value is None:
        return Check(
            "key",
            "API KEY",
            "fail",
            f"no {provider.label} key saved — open SETUP, paste it, SAVE & CHECK KEY",
        )
    return Check(
        "key",
        "API KEY",
        "unchecked",
        f"{provider.label} key saved ({credential.source.value.lower()}); press VERIFY to "
        "test it against the provider",
    )


def _ltspice_located(config) -> Check:
    from boardmodeler.simulation.ltspice import locate_outcome

    try:
        outcome = locate_outcome()
    except Exception:
        outcome = None
    if outcome is None or outcome.install is None:
        return Check(
            "ltspice", "LTSPICE", "fail", "LTspice is not selected — SETUP > BROWSE, then SAVE"
        )
    return Check(
        "ltspice",
        "LTSPICE",
        "unchecked",
        f"selected: {outcome.install.path} — press VERIFY for a smoke run",
    )


def _pdf_reader() -> Check:
    missing = [name for name in ("pypdf", "pypdfium2") if importlib.util.find_spec(name) is None]
    if missing:
        return Check("pdf", "PDF", "fail", f"missing PDF reader package(s): {', '.join(missing)}")
    return Check("pdf", "PDF", "ok", "text PDFs are read with pypdf, with pdfium as fallback")


def _ocr() -> Check:
    try:
        from boardmodeler.documents.ocr import probe_ocr

        unavailable = probe_ocr()
    except Exception:
        unavailable = True
    if unavailable is None:
        return Check("ocr", "OCR", "ok", "Tesseract found: scanned pages can be read")
    return Check(
        "ocr",
        "OCR",
        "warn",
        "Tesseract not found: scanned (image-only) pages are skipped and reported as gaps; "
        "text PDFs are unaffected",
    )


def _internet(config) -> Check:
    try:
        from boardmodeler.security.network import internet_allowed

        allowed = internet_allowed()
    except Exception:
        allowed = bool(getattr(config, "internet_access", False))
    if allowed:
        return Check("internet", "INTERNET", "ok", "INTERNET ACCESS is on")
    return Check(
        "internet",
        "INTERNET",
        "fail",
        "INTERNET ACCESS is off in SETUP (or BOARDMODELER_NO_NETWORK is set): the provider "
        "cannot be reached",
    )


def local_checks(config=None) -> list[Check]:
    """Everything that can be known without sending a request. Never raises."""
    if config is None:
        from boardmodeler.config import load_config

        config = load_config()
    checks = [
        _key_stored(config),
        Check("model", "MODEL", "unchecked", "press VERIFY to test the model's reply format"),
        _ltspice_located(config),
        _pdf_reader(),
        _ocr(),
        _internet(config),
    ]
    return checks


def verify_provider_tools(
    provider,
    key: str,
    *,
    model: str | None = None,
    timeout_s: float = CHECK_TIMEOUT_S,
    transport=None,
) -> tuple[Check, Check]:
    """``(key check, model check)`` from one request shaped exactly like a build's."""
    from boardmodeler import __version__
    from boardmodeler.authoring.api_backend import (
        REPLY_FORMAT_INSTRUCTION,
        ApiKeyBackend,
        HttpRequest,
        _decoded,
        _reply_text,
        parse_files_reply,
        urllib_transport,
    )

    backend = ApiKeyBackend(provider, model=model, max_output_tokens=2048, retries=0)
    switches = None
    if "reasoning_effort" in provider.extra_body:
        switches = {**provider.extra_body, "reasoning_effort": "low"}
    prompt = f"{CAPABILITY_PROMPT}\n\n{REPLY_FORMAT_INSTRUCTION}"
    url, headers, body = backend._shape(prompt, key=key, extra_body=switches)
    request = HttpRequest(
        method="POST",
        url=url,
        headers={
            **headers,
            "Content-Type": "application/json",
            "Accept": "application/json",
            # The builds' own agent string: the endpoint's edge refuses urllib's default.
            "User-Agent": f"SpiceMaker/{__version__}",
            **({provider.session_header: backend.session_id} if provider.session_header else {}),
        },
        body=json.dumps(body).encode("utf-8"),
        timeout_s=timeout_s,
    )
    model_name = backend._model()
    not_run = Check("model", "MODEL", "unchecked", "not tested: the key check did not pass")
    try:
        response = (transport or urllib_transport)(request)
    except Exception:
        return (
            Check("key", "API KEY", "warn", "could not reach the provider (timeout or network)"),
            not_run,
        )
    if response.status in (401, 403):
        return (
            Check(
                "key",
                "API KEY",
                "fail",
                f"{provider.label} rejected the key (HTTP "
                f"{response.status}) — paste a new one in SETUP",
            ),
            not_run,
        )
    if response.status in (402, 429):
        return (
            Check(
                "key",
                "API KEY",
                "warn",
                f"key accepted but HTTP {response.status}: out of balance, quota or rate limit",
            ),
            not_run,
        )
    if response.status in (400, 404, 422):
        return (
            Check("key", "API KEY", "ok", f"{provider.label} answered (HTTP {response.status})"),
            Check(
                "model",
                "MODEL",
                "fail",
                f"the request was refused (HTTP {response.status}): "
                f"check the model id {model_name!r} in SETUP",
            ),
        )
    if not 200 <= response.status < 300:
        return (
            Check("key", "API KEY", "warn", f"provider returned HTTP {response.status}"),
            not_run,
        )
    key_ok = Check("key", "API KEY", "ok", f"{provider.label} accepted the key")
    try:
        payload = _decoded(response, secrets=[key])
        text, _stop = _reply_text(provider.wire, payload)
    except Exception:
        return key_ok, Check(
            "model", "MODEL", "fail", f"{model_name} returned no usable answer to the check"
        )
    files, _problem = parse_files_reply(text)
    if files and any(str(content).strip().upper().startswith("OK") for content in files.values()):
        return key_ok, Check(
            "model",
            "MODEL",
            "ok",
            f"{model_name} answered in the generator's JSON file format",
        )
    return key_ok, Check(
        "model",
        "MODEL",
        "fail",
        f"{model_name} answered, but not in the JSON file format the generator needs",
    )


def _ltspice_smoke(config) -> Check:
    located = _ltspice_located(config)
    if located.state == "fail":
        return located
    from boardmodeler.simulation.ltspice import locate, smoke_test

    try:
        install = locate()
        result = smoke_test(install.path, Path(tempfile.mkdtemp(prefix="bm-ready-")))
    except Exception as exc:
        return Check("ltspice", "LTSPICE", "fail", f"smoke run failed: {type(exc).__name__}")
    if getattr(result, "status", "") == "pass":
        return Check("ltspice", "LTSPICE", "ok", f"{install.path.name} ran the RC smoke circuit")
    return Check("ltspice", "LTSPICE", "fail", f"smoke run: {getattr(result, 'detail', '')}"[:200])


def verify_all(
    config=None, *, cancel: threading.Event | None = None, transport=None
) -> list[Check]:
    """The local checks plus the provider request and an LTspice smoke run."""
    if config is None:
        from boardmodeler.config import load_config

        config = load_config()
    checks = {check.key: check for check in local_checks(config)}
    provider, credential, _problem = _provider_and_key(config)
    if cancel is not None and cancel.is_set():
        return [checks[key] for key in _ORDER]
    if provider is not None and credential is not None and credential.value:
        if checks["internet"].state != "ok":
            checks["key"] = Check(
                "key", "API KEY", "unchecked", "not tested: INTERNET ACCESS is off"
            )
        elif provider.uses_cli:
            from boardmodeler.security.key_verification import verify_key

            result = verify_key(provider, credential.value, cancel=cancel)
            state = {"verified": "ok", "rejected": "fail"}.get(result.status, "warn")
            checks["key"] = Check("key", "API KEY", state, result.detail)
            checks["model"] = Check(
                "model",
                "MODEL",
                state,
                result.detail if state != "ok" else "the agent answered the check",
            )
        else:
            model = (getattr(config, "agent_model", None) or "").strip() or None
            checks["key"], checks["model"] = verify_provider_tools(
                provider, credential.value, model=model, transport=transport
            )
    checks["ltspice"] = _ltspice_smoke(config)
    return [checks[key] for key in _ORDER]

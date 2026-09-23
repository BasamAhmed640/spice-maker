"""The agent providers this build accepts: raw API keys, no login flows.

The list is **data, not code**. A build ships the catalog it sells, and every consumer
(the setup page's provider row, the backend factory, ``doctor``) reads the catalog
instead of naming a provider itself. This general edition ships HTTPS API-key entries
only: the key travels in a request to the host that provider documents, and no entry
names a local process. Bob exists only in the separate Bob-only edition, which has its
own copy of this file; nothing here can select or run a CLI agent.

Every entry declares the transport it needs (``wire``), so an unsupported shape is
refused with a reason instead of being coerced:

``openai``
    ``POST <endpoint>/chat/completions``, ``Authorization: Bearer <key>`` — the shape
    OpenAI documents and several vendors implement. OpenCode Zen is the same shape on
    its own host; the models it serves from ``/responses`` and ``/messages`` are not
    reachable through this wire, and the MODEL row in SETUP is where that choice lives.
``anthropic``
    ``POST <endpoint>/messages`` with ``x-api-key`` and ``anthropic-version``.
``google``
    ``POST <endpoint>/models/<model>:generateContent`` with ``x-goog-api-key``.

Endpoints and default model ids are the vendors' own documented values and are never
guessed (D-005); each entry carries the documentation URL it came from. A model id can
be overridden per machine in SETUP or with ``--model``, because these strings do drift.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

__all__ = [
    "CATALOG",
    "DEFAULT_PROVIDER_ID",
    "AgentProvider",
    "by_id",
    "default_provider",
    "endpoint_is_vendor",
    "ids",
    "only_provider",
    "require",
    "vendor_host",
]

#: Wires this build knows how to speak: every one is an HTTPS request to a vendor's own
#: documented host. A catalog entry naming anything else is a build defect, and
#: ``by_id``/``require`` say so instead of picking a fallback.
WIRES: tuple[str, ...] = ("openai", "anthropic", "google")


@dataclass(frozen=True)
class AgentProvider:
    """One accepted way to reach an agent, and how its key is stored.

    ``credential`` is the name used with :mod:`boardmodeler.security.credentials`
    (plain local entry ``provider:<credential>:api_key`` in this folder's credential
    file, environment fallback
    ``BOARDMODELER_<CREDENTIAL>_API_KEY``); ``env_aliases`` are additional plain
    environment variables the same key is read from, for people who already export
    the vendor's own variable. ``key_label`` and ``key_hint`` are the setup page's
    label and hint, so a build with one provider keeps that provider's own wording.
    """

    id: str
    label: str
    wire: str
    credential: str
    key_label: str
    key_hint: str
    docs: str
    endpoint: str | None = None
    model: str | None = None
    env_aliases: tuple[str, ...] = ()
    #: Documented conversation header for providers that require session affinity.
    session_header: str | None = None
    #: On the ``openai`` wire, whether the entry's model is a reasoning model: it
    #: rejects ``temperature`` and takes its output budget as
    #: ``max_completion_tokens`` rather than ``max_tokens``. OpenAI's own reasoning
    #: models do; most OpenAI-compatible vendors still take the classic parameters,
    #: so this is per entry rather than per wire.
    reasoning: bool = False
    #: Extra fields merged into the request body for this entry, in the vendor's own
    #: documented spelling. This exists because a vendor knob can decide whether the
    #: task is possible at all: DeepSeek's ``{"thinking": {"type": "enabled"},
    #: "reasoning_effort": "low"}`` is the setting measured to produce a model the
    #: harness can judge (0 PASS with thinking off, 4 PASS / 0 FAIL at low effort), and
    #: the same switch will occasionally spend the whole output budget reasoning.
    extra_body: Mapping[str, object] = field(default_factory=dict)
    #: Legacy field retained for compatibility. The backend never uses it to lower
    #: reasoning or switch providers; truncation recovery only grows a known budget.
    retry_body: Mapping[str, object] = field(default_factory=dict)

    @property
    def uses_cli(self) -> bool:
        """Whether the key would be consumed by a local CLI process rather than HTTP.

        False for every entry this edition ships; the distinction stays because the
        key-check dispatcher reads it and the Bob edition's own catalog uses it.
        """
        return self.wire not in WIRES

    @property
    def model_editable(self) -> bool:
        """Whether this provider takes a model id from this application at all."""
        return not self.uses_cli


#: The providers a build accepts, default first; nothing else in the code names a
#: provider. Every entry here is HTTPS: a general-edition user cannot make the app run
#: a local agent process.
CATALOG: tuple[AgentProvider, ...] = (
    AgentProvider(
        id="deepseek",
        label="DeepSeek",
        wire="openai",
        credential="deepseek",
        key_label="DEEPSEEK API KEY",
        key_hint=(
            "platform.deepseek.com → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://api-docs.deepseek.com/",
        endpoint="https://api.deepseek.com",
        model="deepseek-flash",
        env_aliases=("DEEPSEEK_API_KEY",),
        # Maximum documented effort, retained for extraction and every repair.
        # No fallback may silently disable thinking when the output budget is spent.
        extra_body={"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
    ),
    AgentProvider(
        id="openai",
        label="OpenAI",
        wire="openai",
        credential="openai",
        key_label="OPENAI API KEY",
        key_hint=(
            "platform.openai.com → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://developers.openai.com/api/docs/guides/text",
        endpoint="https://api.openai.com/v1",
        model="gpt-6-astra",
        env_aliases=("OPENAI_API_KEY",),
        reasoning=True,
        extra_body={"reasoning_effort": "max"},
    ),
    AgentProvider(
        id="anthropic",
        label="Anthropic",
        wire="anthropic",
        credential="anthropic",
        key_label="ANTHROPIC API KEY",
        key_hint=(
            "console.anthropic.com → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://platform.claude.com/docs/en/get-started",
        endpoint="https://api.anthropic.com/v1",
        model="claude-opus-5",
        env_aliases=("ANTHROPIC_API_KEY",),
        extra_body={"thinking": {"type": "adaptive"}, "output_config": {"effort": "max"}},
    ),
    AgentProvider(
        id="google",
        label="Google Gemini",
        wire="google",
        credential="google",
        key_label="GEMINI API KEY",
        key_hint=(
            "aistudio.google.com → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://ai.google.dev/gemini-api/docs/text-generation",
        endpoint="https://generativelanguage.googleapis.com/v1beta",
        model="gemini-3.8-flash",
        env_aliases=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        extra_body={"generationConfig": {"thinkingConfig": {"thinkingLevel": "high"}}},
    ),
    AgentProvider(
        id="openrouter",
        label="OpenRouter",
        wire="openai",
        credential="openrouter",
        key_label="OPENROUTER API KEY",
        key_hint=(
            "openrouter.ai → keys  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://openrouter.ai/docs/quickstart",
        endpoint="https://openrouter.ai/api/v1",
        model="~openai/gpt-sol-latest",
        env_aliases=("OPENROUTER_API_KEY",),
        extra_body={"reasoning": {"effort": "max"}},
    ),
    AgentProvider(
        id="xai",
        label="xAI",
        wire="openai",
        credential="xai",
        key_label="XAI API KEY",
        key_hint=(
            "console.x.ai → API keys  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://docs.x.ai/developers/models",
        endpoint="https://api.x.ai/v1",
        model="grok-4.6",
        env_aliases=("XAI_API_KEY",),
        extra_body={"reasoning_effort": "xhigh"},
    ),
    AgentProvider(
        id="groq",
        label="Groq",
        wire="openai",
        credential="groq",
        key_label="GROQ API KEY",
        key_hint=(
            "console.groq.com → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://console.groq.com/docs/api-reference",
        endpoint="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        env_aliases=("GROQ_API_KEY",),
    ),
    AgentProvider(
        id="opencode",
        label="OpenCode Zen (pay as you go)",
        wire="openai",
        credential="opencode",
        key_label="OPENCODE API KEY",
        key_hint="opencode.ai/auth → API key with Zen credit; select OpenCode Go for a Go subscription",
        docs="https://opencode.ai/docs/zen",
        endpoint="https://opencode.ai/zen/v1",
        model="deepseek-v4-flash",
        env_aliases=("OPENCODE_API_KEY",),
        session_header="x-opencode-session",
        extra_body={
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    ),
    AgentProvider(
        id="opencode_go",
        label="OpenCode Go (subscription)",
        wire="openai",
        credential="opencode",
        key_label="OPENCODE GO API KEY",
        key_hint="opencode.ai/auth → API key for your Go subscription",
        docs="https://opencode.ai/docs/go/",
        endpoint="https://opencode.ai/zen/go/v1",
        model="deepseek-v4.1-flash",
        env_aliases=("OPENCODE_API_KEY",),
        session_header="x-opencode-session",
        extra_body={
            "thinking": {"type": "enabled"},
            "reasoning_effort": "max",
            "stream": True,
            "stream_options": {"include_usage": True},
        },
    ),
    AgentProvider(
        id="mistral",
        label="Mistral",
        wire="openai",
        credential="mistral",
        key_label="MISTRAL API KEY",
        key_hint=(
            "console.mistral.ai → API keys"
            "  ·  saved in this folder as a plain local file (not encrypted)"
        ),
        docs="https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request",
        endpoint="https://api.mistral.ai/v1",
        model="mistral-large-latest",
        env_aliases=("MISTRAL_API_KEY",),
    ),
)


#: The provider a build expects when the user has not chosen one: the catalog's first
#: entry, so the default follows the catalog instead of naming a provider twice.
DEFAULT_PROVIDER_ID = CATALOG[0].id


def ids() -> tuple[str, ...]:
    """The ids this build accepts, in catalog order."""
    return tuple(provider.id for provider in CATALOG)


def by_id(provider_id: str | None) -> AgentProvider | None:
    """The catalog entry with ``provider_id``, or ``None`` when this build has none."""
    if not provider_id:
        return None
    wanted = str(provider_id).strip().lower()
    for provider in CATALOG:
        if provider.id == wanted:
            return provider
    return None


def require(provider_id: str | None) -> AgentProvider:
    """As :func:`by_id`, but raise for a provider this build does not accept."""
    provider = by_id(provider_id)
    if provider is None:
        raise ValueError(
            f"provider {provider_id!r} is not accepted by this build; accepted: {list(ids())}"
        )
    return provider


def default_provider() -> AgentProvider:
    """The default entry: :data:`DEFAULT_PROVIDER_ID`, or the only entry there is."""
    provider = by_id(DEFAULT_PROVIDER_ID)
    if provider is not None:
        return provider
    if not CATALOG:  # pragma: no cover - a build with no provider cannot run anything
        raise ValueError("this build has an empty provider catalog")
    return CATALOG[0]


def vendor_host(url: str | None) -> str | None:
    """The host a URL (or bare host) names, normalized for comparison.

    Comparison never cares about scheme, port, case, a trailing root dot or
    userinfo: only the host decides. A value this function cannot parse yields
    ``None``, and a caller that needs a decision must refuse rather than treat
    ``None`` as a match.
    """
    if not url or not isinstance(url, str):
        return None
    text = url.strip()
    if not text:
        return None
    parts = urlsplit(text)
    if parts.hostname is None:  # a bare ``api.example.com/v1`` has no scheme to split
        parts = urlsplit(f"//{text}")
    host = parts.hostname
    if host is None:
        return None
    return host.strip().strip(".").lower() or None


def _is_loopback_host(host: str) -> bool:
    """True for ``localhost`` and every loopback address: never internet egress."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool((mapped if mapped is not None else address).is_loopback)


def endpoint_is_vendor(provider: str | None, url: str) -> tuple[bool, str]:
    """``(allowed, reason)`` for one inference destination.

    This is the single source of truth for inference egress: the host a request
    would go to must be exactly the host the selected :data:`CATALOG` entry
    declares in its documented ``endpoint``. Scheme and port are ignored; the
    host is compared as a whole, so ``https://api.deepseek.com.evil.test/v1``
    and ``https://evilapi.deepseek.com/v1`` both fail against
    ``api.deepseek.com`` (no suffix, substring or registrable-domain match — a
    lookalike domain is a different domain).

    A provider id outside the catalog declares no vendor host at all, so the
    only destination it may reach is a loopback one (a local fixture or test
    server, which is not internet egress); a public destination is refused
    rather than guessed. An entry with no HTTP endpoint (a wire this edition
    does not serve as HTTPS) is refused for any URL. The reason names the
    provider, the expected host and the documentation it came from, so a refusal
    is reported instead of retried.
    """
    host = vendor_host(url)
    if host is None:
        return False, f"endpoint_host_missing: {url!r} names no host to match"
    entry = by_id(provider)
    if entry is None:
        if _is_loopback_host(host):
            return True, f"local_endpoint: {host!r} is loopback, not internet egress"
        named = "<none>" if not provider else str(provider)
        return False, (
            f"provider_not_in_catalog: provider {named!r} is not a provider this build "
            f"accepts ({list(ids())}), so no vendor host is declared; refusing the internet "
            f"destination {host!r}"
        )
    if not entry.endpoint:
        return False, (
            f"provider_has_no_http_endpoint: provider {entry.id!r} uses the {entry.wire!r} wire "
            f"and declares no HTTP endpoint (documentation: {entry.docs}); refusing {host!r}"
        )
    expected = vendor_host(entry.endpoint)
    if expected is None:  # pragma: no cover - a catalog entry with an unusable endpoint
        return False, (
            f"provider_endpoint_unusable: provider {entry.id!r} declares an endpoint with no "
            f"host ({entry.endpoint!r}); refusing {host!r}"
        )
    if host != expected:
        return False, (
            f"endpoint_not_vendor: provider {entry.id!r} declares host {expected!r} "
            f"(documentation: {entry.docs}); refusing {host!r}"
        )
    return True, f"vendor_endpoint: {host!r} is the host provider {entry.id!r} declares"


def only_provider() -> AgentProvider | None:
    """The one provider a restricted build accepts, or ``None`` when there is a choice.

    A build whose catalog holds a single entry offers no provider choice, so the surfaces
    that would offer one say whose build this is instead: the SETUP page says it accepts
    that provider's API only, and the window title carries the same fact (D-015).
    """
    return CATALOG[0] if len(CATALOG) == 1 else None

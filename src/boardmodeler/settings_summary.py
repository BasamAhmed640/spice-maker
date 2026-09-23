"""The persisted settings as data, importable without Qt.

``boardmodeler setup --json`` is the only way to read the settings from the command
line, and it has to work in the ``.venv`` the installer builds: that environment is
deliberately Qt-free (see ``installer/vendor_env.py``), so importing
:mod:`boardmodeler.ui.setup_dialog` to reach its descriptions raised
``ModuleNotFoundError: No module named 'PySide6'`` in exactly the copy the product
ships. The descriptions therefore live here, where nothing imports a GUI toolkit, and
the dialog module re-exports them so there is still one implementation.
"""

from __future__ import annotations

import os
from pathlib import Path

from boardmodeler import agent_providers
from boardmodeler import config as _config
from boardmodeler.agent_providers import AgentProvider
from boardmodeler.config import AppConfig
from boardmodeler.storage import app_root, library_dir, portable

__all__ = [
    "configured_provider",
    "credential_file_label",
    "describe_settings",
    "ltspice_user_lib",
]


def ltspice_user_lib(home: Path | None = None) -> Path:
    """The per-user LTspice library (never the installation directory)."""
    if portable():
        return library_dir()
    base = home if home is not None else Path.home()
    return base / "AppData" / "Local" / "LTspice" / "lib"


def credential_file_label() -> str:
    """Where the API key is saved, relative to this extracted folder when it is inside it.

    Resolved, never hard-coded: the Bob edition writes a different file name, and the
    page must not name a file that this build does not use.
    """
    from boardmodeler.security.credentials import credential_path

    path = credential_path()
    try:
        return str(path.relative_to(app_root())).replace(os.sep, "/")
    except ValueError:  # pragma: no cover - the credential file is always inside the copy
        return str(path)


def configured_provider(config: AppConfig) -> tuple[AgentProvider | None, str]:
    """``(provider, reason)`` for ``config.agent_provider``; never another provider.

    An id this build does not accept comes back as ``None`` plus the same
    ``api_provider_unavailable`` text
    :func:`boardmodeler.authoring.api_backend.build_api_backend` refuses with, so
    the page, the window and the engine cannot disagree about the refusal. An
    empty setting means this build's default provider.
    """
    wanted = str(config.agent_provider or "").strip()
    if not wanted:
        return agent_providers.default_provider(), ""
    provider = agent_providers.by_id(wanted)
    if provider is not None:
        return provider, ""
    accepted = ", ".join(repr(name) for name in agent_providers.ids()) or "none"
    return None, (
        f"api_provider_unavailable: {wanted!r} is not a provider this build accepts; "
        f"use one of {accepted}"
    )


def describe_settings(config: AppConfig) -> dict[str, object]:
    """The persisted settings as data, for ``boardmodeler setup --json`` and tests."""
    from boardmodeler.security.credentials import describe_credential

    provider, reason = configured_provider(config)
    shown = provider or agent_providers.default_provider()
    return {
        # Through the module, not a name bound at import time: a test or a caller that
        # redirects ``boardmodeler.config.config_path`` must redirect this report too,
        # and an early binding silently ignored the redirect.
        "config_path": str(_config.config_path()),
        "ltspice_path": config.ltspice.path,
        "model_dir": config.default_model_dir,
        "internet_access": config.internet_access,
        "ltspice_user_lib": str(ltspice_user_lib()),
        # The id the config names, never a substitute; the flag says whether this
        # build accepts it, so a caller sees the refusal instead of another provider.
        "agent_provider": str(config.agent_provider or "").strip() or shown.id,
        "agent_provider_accepted": provider is not None,
        "agent_provider_problem": reason,
        "agent_model": config.agent_model or shown.model,
        "agent_api_key": describe_credential(shown.credential),
        "accepted_providers": list(agent_providers.ids()),
    }

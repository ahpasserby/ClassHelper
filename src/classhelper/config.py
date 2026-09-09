"""Configuration, loaded from ~/.classhelper/config.toml.

Deliberately outside the project directory: an API key that lives next to the
source is an API key that eventually gets committed. Environment variables
override the file, which is what CI and container setups need.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

def _project_root() -> Path:
    """The directory holding ClassHelper.command, when there is one.

    Settings live beside the launcher rather than in a hidden directory: this
    is a program you keep in a folder and start by double-clicking it, so the
    folder is where you would look for its configuration. Falls back to a
    home-directory path when installed as a plain package, where there is no
    such folder to speak of.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "ClassHelper.command").exists() or (
            candidate / "pyproject.toml"
        ).exists():
            return candidate
    return Path.home() / ".classhelper"


CONFIG_DIR = Path(os.environ.get("CLASSHELPER_HOME", _project_root()))
CONFIG_PATH = CONFIG_DIR / "config.toml"

# Everything the user owns lives under one directory they choose, split in two:
# the material itself, and the program's state about it. One place to point at
# a synced folder, one place to back up.
DEFAULT_USER_DATA = Path.home() / "classhelper"
LIBRARY_DIRNAME = "课板"
STATE_DIRNAME = "用户配置"

# Set once the configuration is known, so the modules that keep per-user state
# do not each have to read the config file.
_state_dir: Path | None = None


def use_state_dir(path: Path) -> None:
    global _state_dir
    _state_dir = Path(path)
    _state_dir.mkdir(parents=True, exist_ok=True)


def state_dir() -> Path:
    """Where the program keeps its own files: caches, fallbacks, uploads."""
    if _state_dir is None:
        try:
            use_state_dir(Path(load().state_path))
        except ConfigError:
            use_state_dir(DEFAULT_USER_DATA / STATE_DIRNAME)
    assert _state_dir is not None
    return _state_dir

# Per-provider defaults, so a config file only has to name the provider and a
# key. `ask_model` is separate on purpose: translating a deck is thousands of
# cheap calls, while answering a question is one call where reasoning is worth
# paying for.
PROVIDER_DEFAULTS = {
    # Measured, not guessed. DeepSeek's /models lists only the v4 family, but
    # those are reasoning models: on one slide of twelve sentences they emit
    # ~6000 output tokens and take 45-120s, against 175 tokens and 2.2s for
    # deepseek-chat, for no better translation. Reasoning is worth paying for
    # when answering a question and pure waste when translating a bullet, so
    # the two defaults differ deliberately.
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "ask_model": "deepseek-reasoner",
        "env_key": "DEEPSEEK_API_KEY",
        "label": "DeepSeek",
        # Offered alongside whatever /models returns, because a provider's
        # listing can omit a working endpoint -- deepseek-chat is not in it.
        "known_models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "ask_model": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
        "label": "OpenAI",
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "ask_model": "moonshot-v1-32k",
        "env_key": "MOONSHOT_API_KEY",
        "label": "Moonshot 月之暗面",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "ask_model": "Qwen/Qwen2.5-72B-Instruct",
        "env_key": "SILICONFLOW_API_KEY",
        "label": "硅基流动",
    },
    "ollama": {
        # Local models. No key required, but the field is still sent because
        # the OpenAI protocol expects a bearer token.
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5",
        "ask_model": "qwen2.5",
        "env_key": "OLLAMA_API_KEY",
        "label": "Ollama（本地）",
        "keyless": True,
    },
    "custom": {
        "base_url": "",
        "model": "",
        "ask_model": "",
        "env_key": "CLASSHELPER_API_KEY",
        "label": "自定义（任何 OpenAI 兼容接口）",
    },
}


class ConfigError(Exception):
    """Raised with a message meant to be shown to the user verbatim."""


@dataclass
class Config:
    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    ask_model: str = ""
    source_lang: str = "auto"
    target_lang: str = "zh-CN"
    # Keep terms that are conventionally left in the source language alone.
    # Users add course-specific ones; these are just a sensible floor.
    keep_verbatim: list[str] = field(default_factory=list)
    max_workers: int = 4
    # Only needed behind a TLS-intercepting proxy; see tls.py.
    ca_bundle: str = ""
    # One directory the user chooses, holding both of the below.
    user_data_path: str = ""
    # What importing does to the original file: move, copy or link.
    import_mode: str = "move"
    # How the window opens: "fullscreen" or "maximized".
    window_mode: str = "fullscreen"

    @property
    def library_path(self) -> str:
        """The course material: folders, decks, and their per-folder state."""
        return str(Path(self.user_data_path or DEFAULT_USER_DATA) / LIBRARY_DIRNAME)

    @property
    def state_path(self) -> str:
        """The program's own files about that material."""
        return str(Path(self.user_data_path or DEFAULT_USER_DATA) / STATE_DIRNAME)

    @property
    def defaults(self) -> dict:
        return PROVIDER_DEFAULTS.get(self.provider, {})


def save(values: dict, path: Path | None = None) -> None:
    """Write the settings file.

    Generated rather than edited in place: this file is small, fully described
    by the settings page, and round-tripping user comments is not worth the
    machinery. The header says as much so nobody puts notes here expecting them
    to survive.

    Written 0600, because it holds an API key.
    """
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    # Keep the previous file. This holds the only copy of an API key, and a bad
    # save once wiped a working one with no way back.
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        backup.chmod(0o600)

    def quote(value: str) -> str:
        return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'

    translate = {k: v for k, v in values.items()
                 if k not in {"ask_model", "user_data_path", "import_mode",
                              "window_mode"}
                 and v not in (None, "")}
    lines = [
        "# classhelper settings. Written by the app's settings page --",
        "# hand edits survive, comments do not.",
        "",
        "[translate]",
        *(f"{key} = {quote(value)}" for key, value in translate.items()),
    ]
    if values.get("ask_model"):
        lines += ["", "[ask]", f"model = {quote(values['ask_model'])}"]

    board = {k: values[k] for k in ("user_data_path", "import_mode")
             if values.get(k)}
    if board:
        lines += ["", "[board]",
                  *(f"{key} = {quote(value)}" for key, value in board.items())]
    if values.get("window_mode"):
        lines += ["", "[window]", f"mode = {quote(values['window_mode'])}"]

    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    raw: dict = {}

    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            raise ConfigError(f"{path} could not be read: {exc}") from exc

    section = raw.get("translate", {})
    cfg = Config(
        provider=section.get("provider", "deepseek"),
        api_key=section.get("api_key", ""),
        source_lang=section.get("source_lang", "auto"),
        target_lang=section.get("target_lang", "zh-CN"),
        keep_verbatim=list(section.get("keep_verbatim", [])),
        max_workers=int(section.get("max_workers", 4)),
        ca_bundle=section.get("ca_bundle", ""),
    )
    board = raw.get("board", {})
    cfg.user_data_path = str(
        Path(board.get("user_data_path") or DEFAULT_USER_DATA).expanduser()
    )
    cfg.import_mode = board.get("import_mode", "move")
    cfg.window_mode = raw.get("window", {}).get("mode", "fullscreen")

    defaults = cfg.defaults
    if not defaults:
        known = ", ".join(sorted(PROVIDER_DEFAULTS))
        raise ConfigError(f"Unknown provider {cfg.provider!r}. Known: {known}.")

    cfg.base_url = section.get("base_url") or defaults["base_url"]
    cfg.model = section.get("model") or defaults["model"]
    cfg.ask_model = raw.get("ask", {}).get("model") or defaults["ask_model"]

    # Environment wins over the file.
    cfg.api_key = (
        os.environ.get("CLASSHELPER_API_KEY")
        or os.environ.get(defaults.get("env_key", ""), "")
        or cfg.api_key
    )

    if not cfg.api_key and not defaults.get("keyless"):
        raise ConfigError(
            f"No API key. Put one in {path} as:\n\n"
            f"  [translate]\n  provider = \"{cfg.provider}\"\n"
            f"  api_key = \"sk-...\"\n\n"
            f"or set {defaults.get('env_key')} in the environment."
        )
    return cfg

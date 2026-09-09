"""Shared fixtures.

The one rule here is that the suite must never touch the developer's own data.
That has gone wrong three times -- a test wrote into the real board, another
relocated the real library -- so the state directory and the spending ledger are
redirected for every test, whether it asks for it or not.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches

from classhelper import config as config_module
from classhelper import spend as spend_module
from classhelper.config import Config
from classhelper.providers.base import Usage


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point every per-user file at a scratch directory for the whole test."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_module, "_state_dir", state)
    monkeypatch.setattr(config_module, "DEFAULT_USER_DATA", tmp_path / "userdata")

    # A ledger built from the scratch directory, and put back afterwards so one
    # test's ledger cannot leak into the next.
    previous = spend_module._ledger
    spend_module.use_ledger(
        spend_module.Ledger(state / "spend.db",
                            table=spend_module.Table(state / "pricing.json"))
    )
    yield
    spend_module.use_ledger(previous)


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.usage = Usage()
        self.calls = 0

    def complete(self, system, user, **kwargs):
        self.calls += 1
        ids = [
            int(line.split("]")[0].lstrip("["))
            for line in user.splitlines()
            if line.startswith("[")
        ]
        return json.dumps({"units": {str(i): f"译文{i}" for i in ids}})

    def stream(self, messages, **kwargs):
        yield "答"
        yield "案"


@pytest.fixture
def deck_file(tmp_path):
    prs = Presentation()
    for i in range(3):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = f"Slide {i + 1}"
        box = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(6), Inches(2))
        box.text_frame.text = f"A relationship set appears on slide {i + 1}."
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    return str(path)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLASSHELPER_HOME", str(tmp_path / "home"))

    import classhelper.config as config_module
    import classhelper.server.app as app_module
    import classhelper.server.board_api as board_module
    import classhelper.server.session as session_module
    from classhelper.library import Library

    home = tmp_path / "home"
    # Both of these are module globals, so patching the config file alone is
    # not enough -- without them every test writes into the real library and
    # the developer's own caches.
    # Set through monkeypatch rather than use_state_dir() so it is restored
    # afterwards; a leaked global would point later tests at this tmp dir.
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config_module, "_state_dir", home)
    board_module.use_library(Library(tmp_path / "library"))
    monkeypatch.setattr(config_module, "CONFIG_DIR", home)
    monkeypatch.setattr(
        app_module, "load_config",
        lambda: Config(provider="fake", api_key="x", model="m",
                       ask_model="m", target_lang="zh-CN", max_workers=2),
    )
    provider = FakeProvider()
    monkeypatch.setattr(session_module, "build_provider", lambda cfg: provider)
    monkeypatch.setattr(app_module, "store", session_module.SessionStore())


    with TestClient(app_module.app) as test_client:
        test_client.provider = provider  # type: ignore[attr-defined]
        yield test_client

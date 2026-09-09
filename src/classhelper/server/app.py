"""Local HTTP API behind the reader.

Bound to loopback and single-user by design: this is the back half of a desktop
application that happens to render in a browser, not a service. No auth, because
there is no second user; no CORS, because the page is served from here.

Long-running work is reported over server-sent events rather than made into
blocking requests, so the reader can show slide 3 while slides 4 to 32 are still
being translated.
"""

from __future__ import annotations

import asyncio
import json
import queue
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import (
    CONFIG_PATH,
    DEFAULT_USER_DATA,
    state_dir,
    PROVIDER_DEFAULTS,
    Config,
    ConfigError,
    load as load_config,
    save as save_config,
)
from ..library import LibraryError
from ..parsers import SUPPORTED, UnsupportedFormat
from ..providers import ProviderError
from ..library import Library
from .board_api import (
    board_payload,
    glossary_router,
    item_payload,
    library,
    make_glossary,
    router as board_router,
    scope_labels,
    scopes_for,
    use_library,
)
from .session import Event, SessionStore, page_payload

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="classhelper", docs_url=None, redoc_url=None)
store = SessionStore()


def _in_library(path: str) -> str | None:
    """The library-relative folder holding `path`, or None if it is outside."""
    try:
        return library().folder_of(library().relative(Path(path)))
    except (HTTPException, LibraryError, ValueError):
        return None


def _apply_paths(cfg) -> None:
    """Point the library and the program's own state at the configured place."""
    from ..config import use_state_dir

    use_state_dir(Path(cfg.state_path))
    use_library(Library(Path(cfg.library_path)))


def _scopes_for_path(path: str) -> list[str] | None:
    """The glossary chain for a deck, from where it sits in the library.

    A deck opened from outside the library gets None and falls back to a scope
    derived from its folder on disk -- terminology still accumulates, it is
    just not organised until the deck is imported.
    """
    folder = _in_library(path)
    return None if folder is None else library().chain(folder or None)


def _meta_dir(path: str) -> Path | None:
    """Where this deck's folder keeps its cached translations."""
    folder = _in_library(path)
    return None if folder is None else library().meta_dir(folder or None)


def _resolve_deck(raw: str) -> Path:
    """Accept either an absolute path or one relative to the library."""
    direct = Path(raw).expanduser()
    if direct.is_absolute() and direct.exists():
        return direct
    try:
        inside = library().resolve(raw)
        if inside.exists():
            return inside
    except (HTTPException, LibraryError):
        pass
    return direct


def _config():
    try:
        return load_config()
    except ConfigError as exc:
        # 503 rather than 500: the server is fine, it is unconfigured, and the
        # message is written to be shown to the user as-is.
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _session(deck_id: str):
    session = store.get(deck_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No such deck is open.")
    return session


# -- opening a deck --------------------------------------------------------

class OpenRequest(BaseModel):
    path: str


@app.post("/api/open")
def open_deck(body: OpenRequest):
    path = _resolve_deck(body.path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path} does not exist.")
    try:
        session = store.open(
            str(path), _config(), _scopes_for_path(str(path)), _meta_dir(str(path))
        )
    except UnsupportedFormat as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Could not read {path.name}: {exc}"
        ) from exc

    session.start()
    # Opening a deck is the start of hundreds of billed calls, so it is the
    # other moment worth knowing the price. Same weekly guard either way.
    _refresh_prices_quietly(session)
    return session.payload()


# Dropped files arrive as bytes, not paths, so they are kept here. Content-
# addressed: dropping the same deck twice reuses one file and, because the
# translation cache is keyed on text, costs nothing the second time.


# Enough for a large image-heavy deck, small enough that a mis-drop of a video
# fails immediately rather than filling the disk.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024


@app.post("/api/upload")
async def upload(files: list[UploadFile]):
    """Import dropped files into the library's inbox. Nothing is opened.

    Import mode does not apply here: a browser hands over the bytes and not a
    path, so there is no original on disk to move, copy or link.
    """
    _config()  # fail early and clearly if the app is not configured yet
    lib = library()
    added, failed = [], []

    for upload_file in files:
        name = Path(upload_file.filename or "deck").name
        data = await upload_file.read()
        if not data:
            failed.append({"name": name, "error": "文件是空的"})
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            failed.append({"name": name, "error": "文件超过 200 MB"})
            continue
        try:
            added.append(item_payload(lib.import_bytes(name, data)))
        except LibraryError as exc:
            failed.append({"name": name, "error": str(exc)})

    if not added and failed:
        raise HTTPException(status_code=422,
                            detail="；".join(f["error"] for f in failed))
    return {"added": added, "failed": failed, "board": board_payload()}


@app.delete("/api/deck/{deck_id}")
def close_deck(deck_id: str):
    store.close(deck_id)
    return {"ok": True}


class ImportRequest(BaseModel):
    paths: list[str]
    dest: str | None = None


@app.post("/api/import")
def import_paths(body: ImportRequest):
    """Bring files chosen from the system dialog into the library.

    Honours the configured import mode -- move, copy or link -- because these
    have a real original on disk, unlike a browser drop.
    """
    from .board_api import import_paths as do_import
    from .board_api import ImportPaths

    try:
        mode = load_config().import_mode
    except ConfigError:
        mode = "move"
    return do_import(ImportPaths(paths=body.paths, dest=body.dest), mode=mode)


@app.get("/api/deck/{deck_id}")
def get_deck(deck_id: str):
    return _session(deck_id).payload()


@app.post("/api/deck/{deck_id}/focus/{page}")
def focus(deck_id: str, page: int):
    """Tell the scheduler which page is on screen, so it translates that next."""
    session = _session(deck_id)
    session.focus(page)
    return {"ok": True, "progress": session.progress}


@app.get("/api/deck/{deck_id}/image/{image_id}")
def image(deck_id: str, image_id: str):
    img = _session(deck_id).image(image_id)
    if img is None:
        raise HTTPException(status_code=404, detail="No such image.")
    return Response(
        content=img.data,
        media_type=f"image/{'jpeg' if img.ext in ('jpg', 'jpeg') else img.ext}",
        headers={"Cache-Control": "max-age=86400"},
    )


@app.get("/api/deck/{deck_id}/source")
def source_status(deck_id: str):
    """Whether the slide view can show the file itself, and if not, why not."""
    return _session(deck_id).renderer.source.status()


@app.get("/api/deck/{deck_id}/source/{page}")
async def source_page(deck_id: str, page: int, w: int = 1200):
    """One source page as a picture.

    404 when there is nothing exact to show -- the reader falls back to drawing
    the page from the extracted geometry, which is the honest thing to display
    when we do not have the real one.

    Rasterising is CPU work, so it goes to a thread: done inline it would pin
    the event loop and stall every other request while a page is drawn.
    """
    session = _session(deck_id)
    if not 0 <= page < len(session.deck.pages):
        raise HTTPException(status_code=404, detail="No such page.")

    png = await asyncio.to_thread(session.renderer.render, page, w)
    if png is None:
        raise HTTPException(status_code=404, detail="No rendered source.")
    return Response(
        content=png,
        media_type="image/png",
        # Keyed on the file's own mtime and size, so editing the deck and
        # reopening it cannot serve yesterday's picture.
        headers={
            "Cache-Control": "max-age=86400",
            "ETag": f'"{session.renderer.etag}-{page}-{w}"',
        },
    )


# -- live updates ----------------------------------------------------------

@app.get("/api/deck/{deck_id}/events")
async def events(deck_id: str, request: Request):
    session = _session(deck_id)

    # Subscribe *before* snapshotting. Anything translated in between lands in
    # the queue and is delivered after the snapshot already containing it --
    # harmless, because every event carries a whole page rather than a delta.
    # Snapshotting first would lose those pages instead.
    channel = session.subscribe()
    snapshot = session.payload()

    async def stream():
        try:
            # Without this the client can miss everything. A fully cached deck
            # finishes translating in milliseconds, long before the browser has
            # opened this connection, so a stream that only carries future
            # events would leave every sentence showing "translating..." for a
            # document that is already done. It also makes reconnection correct
            # for free: EventSource retries on its own and resyncs here.
            yield _sse(Event("sync", snapshot))
            while not await request.is_disconnected():
                # The queue is filled by translation worker *threads*, so
                # waiting on it is a blocking call. Handed to a worker thread
                # rather than awaited inline: a blocking get() inside an async
                # handler pins the event loop for its full timeout, and with a
                # reader holding this connection open that starves every other
                # request in the process -- answers included. It looked exactly
                # like a slow model.
                try:
                    event = await asyncio.to_thread(channel.get, True, 1.0)
                except queue.Empty:
                    yield ": keep-alive\n\n"  # keeps proxies and tabs honest
                    continue
                yield _sse(event)
        finally:
            session.unsubscribe(channel)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(event: Event) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"


# -- corrections -----------------------------------------------------------

class TermRequest(BaseModel):
    term: str
    translation: str


@app.post("/api/deck/{deck_id}/glossary")
def lock_term(deck_id: str, body: TermRequest):
    """Pin a term and rebuild every sentence that used it."""
    session = _session(deck_id)
    if not body.term.strip() or not body.translation.strip():
        raise HTTPException(status_code=400, detail="Term and translation required.")
    try:
        results = session.translator.lock_term(
            session.deck, body.term.strip(), body.translation.strip()
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    changed = [r.page for r in results]
    for index in changed:
        session.emit(Event("page", {
            "page": index,
            "blocks": page_payload(session.deck.pages[index]),
            "errors": [],
            "progress": session.progress,
        }))
    return {"pages_changed": changed,
            "sentences": sum(r.translated for r in results)}


@app.get("/api/deck/{deck_id}/glossary")
def get_glossary(deck_id: str):
    session = _session(deck_id)
    return {"terms": [
        {"term": term, "translation": e.translation,
         "locked": e.locked, "count": e.count}
        for term, e in session.glossary.items()
    ]}


@app.delete("/api/deck/{deck_id}/glossary/{term}")
def unlock_term(deck_id: str, term: str):
    _session(deck_id).glossary.unset(term)
    return {"ok": True}


@app.post("/api/deck/{deck_id}/retranslate/{sentence_id}")
def retranslate(deck_id: str, sentence_id: str):
    session = _session(deck_id)
    found = session.find_sentence(sentence_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such sentence.")
    page, sentence = found
    try:
        session.translator.retranslate(session.deck, page, sentence)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"id": sentence.id, "translation": sentence.translation,
            "flagged": sentence.flagged}


class EditRequest(BaseModel):
    translation: str


@app.put("/api/deck/{deck_id}/sentence/{sentence_id}")
def edit_sentence(deck_id: str, sentence_id: str, body: EditRequest):
    """Accept the user's own wording, and remember it.

    Writing the edit into the cache is what makes it stick: reopening the deck
    shows what they wrote, not what the model said.
    """
    session = _session(deck_id)
    found = session.find_sentence(sentence_id)
    if found is None:
        raise HTTPException(status_code=404, detail="No such sentence.")
    _, sentence = found
    sentence.translation = body.translation
    sentence.edited = True
    sentence.flagged = False
    session.cache.put(sentence.text, session.cfg.target_lang,
                      session.cfg.model, body.translation)
    return {"ok": True}


# -- saved conversations ---------------------------------------------------

def _chats(session):
    from ..chats import for_deck as chats_for_deck

    return chats_for_deck(session.path, _meta_dir(session.path))


class ChatMessage(BaseModel):
    # assistant-ui's exported repository item, stored verbatim.
    parentId: str | None = None
    message: dict


class ChatTitle(BaseModel):
    title: str


@app.get("/api/deck/{deck_id}/chats")
def list_chats(deck_id: str):
    return {"chats": _chats(_session(deck_id)).list()}


@app.post("/api/deck/{deck_id}/chats")
def create_chat(deck_id: str):
    return _chats(_session(deck_id)).create().summary()


@app.get("/api/deck/{deck_id}/chats/{chat_id}")
def read_chat(deck_id: str, chat_id: str):
    conversation = _chats(_session(deck_id)).get(chat_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="这段对话不存在。")
    return {"id": conversation.id, "title": conversation.title,
            "messages": conversation.messages}


@app.post("/api/deck/{deck_id}/chats/{chat_id}/messages")
def append_chat_message(deck_id: str, chat_id: str, body: ChatMessage):
    """Append one message as the exchange happens.

    Written per message rather than at the end of a run, so an answer survives
    the window being closed halfway through it.
    """
    conversation = _chats(_session(deck_id)).append(
        chat_id, {"parentId": body.parentId, "message": body.message}
    )
    return conversation.summary() if conversation else {"id": chat_id}


@app.put("/api/deck/{deck_id}/chats/{chat_id}")
def rename_chat(deck_id: str, chat_id: str, body: ChatTitle):
    _chats(_session(deck_id)).rename(chat_id, body.title)
    return {"ok": True}


@app.delete("/api/deck/{deck_id}/chats/{chat_id}")
def delete_chat(deck_id: str, chat_id: str):
    _chats(_session(deck_id)).delete(chat_id)
    return {"ok": True}


# -- asking ----------------------------------------------------------------

class BoardTermRequest(BaseModel):
    term: str
    translation: str
    scope: str | None = None


# Sending a whole deck as context would be a bill, not a feature.
MAX_CONTEXT_PAGES = 24


class AskRequest(BaseModel):
    page: int
    question: str
    sentence_id: str | None = None
    # Several sentences can be selected and asked about together -- "how do
    # these two differ" is a question the reader should be able to carry.
    sentence_ids: list[str] = []
    # Slides picked out in the chapter list, as context. Empty means this one.
    pages: list[int] = []
    history: list[dict] = []


@app.post("/api/deck/{deck_id}/ask")
def ask(deck_id: str, body: AskRequest):
    session = _session(deck_id)
    if not 0 <= body.page < len(session.deck.pages):
        raise HTTPException(status_code=404, detail="No such page.")

    # `sentence_id` is the older single-selection form; both are accepted so
    # one is not a breaking change for the other.
    wanted = list(body.sentence_ids) or (
        [body.sentence_id] if body.sentence_id else []
    )
    sentences = []
    for sentence_id in wanted:
        found = session.find_sentence(sentence_id)
        if found:
            sentences.append(found[1])

    # Capped rather than refused: a question sent with half the deck attached
    # should still be answered, just not billed as though it were the whole one.
    chosen = sorted({p for p in body.pages if 0 <= p < len(session.deck.pages)})
    chosen = chosen[:MAX_CONTEXT_PAGES]

    _refresh_prices_quietly(session)

    def stream():
        try:
            for chunk in session.asker.stream(
                session.deck,
                session.deck.pages[body.page],
                body.question,
                sentences,
                body.history,
                chosen,
            ):
                yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        except ProviderError as exc:
            # Reported in-band: the stream has already started, so an HTTP
            # status can no longer say anything.
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- glossary, across board scopes -----------------------------------------

@glossary_router.get("")
def read_glossary(scope: str | None = None):
    """Every term that applies at `scope`, including what it inherits."""
    scoped = make_glossary(_config(), scope)
    return {
        "scope": scoped.current_scope,
        "chain": scope_labels(scoped.scopes),
        "terms": [
            {"term": r.term, "translation": r.translation, "locked": r.locked,
             "count": r.count, "scope": r.scope, "inherited": r.inherited}
            for r in scoped.resolved()
        ],
    }


@glossary_router.post("")
def set_term(body: BoardTermRequest):
    """Pin a term at one scope, and rebuild the decks that used it.

    Only decks whose scope chain contains the edited scope are affected, which
    is what keeps a course-level correction out of an unrelated course.
    """
    target = body.scope or ""
    scoped = make_glossary(_config(), target)
    if not body.term.strip() or not body.translation.strip():
        raise HTTPException(status_code=400, detail="术语和译法都要填。")
    scoped.set(body.term.strip(), body.translation.strip(), scope=target)

    changed = []
    for session in list(store.all()):
        if target not in (session.scopes or [""]):
            continue
        try:
            results = session.translator.lock_term(
                session.deck, body.term.strip(), body.translation.strip()
            )
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        for result in results:
            session.emit(Event("page", {
                "page": result.page,
                "blocks": page_payload(session.deck.pages[result.page]),
                "errors": result.errors,
                "progress": session.progress,
            }))
        if results:
            changed.append({"deck": session.id,
                            "sentences": sum(r.translated for r in results)})
    return {"decks_changed": changed}


@glossary_router.delete("/{term}")
def unset_term(term: str, scope: str | None = None):
    """Drop a term from one scope, revealing whatever it was overriding."""
    target = scope or ""
    make_glossary(_config(), target).unset(term, scope=target)
    return {"ok": True}


# -- settings --------------------------------------------------------------

# The key is never sent back in full. The page needs to show that one is set
# and let it be replaced; it does not need to be able to read it.
_KEEP_KEY = "__unchanged__"


def _mask(key: str) -> str:
    if not key:
        return ""
    return f"{key[:5]}…{key[-4:]}" if len(key) > 12 else "…"


class SettingsRequest(BaseModel):
    provider: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    ask_model: str = ""
    target_lang: str = ""
    user_data_path: str = ""
    import_mode: str = ""
    window_mode: str = ""


@app.get("/api/settings")
def read_settings():
    """Current settings, plus what the page needs to offer as choices."""
    try:
        cfg = load_config()
        configured = True
    except ConfigError:
        # Nothing stored yet. Show the provider's defaults rather than three
        # blank fields: a first run should look like a program with sensible
        # settings and no key, not like one that lost its settings.
        cfg = Config()
        spec = PROVIDER_DEFAULTS.get(cfg.provider, {})
        cfg.base_url = spec.get("base_url", "")
        cfg.model = spec.get("model", "")
        cfg.ask_model = spec.get("ask_model", "")
        configured = False

    return {
        "configured": configured,
        "path": str(CONFIG_PATH),
        "provider": cfg.provider,
        "base_url": cfg.base_url,
        "model": cfg.model,
        "ask_model": cfg.ask_model,
        "target_lang": cfg.target_lang,
        "user_data_path": cfg.user_data_path or str(DEFAULT_USER_DATA),
        "library_path": cfg.library_path,
        "state_path": cfg.state_path,
        "import_mode": cfg.import_mode,
        "window_mode": cfg.window_mode,
        "import_modes": [
            {"id": "move", "label": "移动",
             "hint": "原文件搬进课板。默认——课件应该只有一份，留在下载文件夹里的那份迟早会和这份不一致。"},
            {"id": "copy", "label": "复制",
             "hint": "原文件留在原地，课板里放一份副本。适合共享盘上不该动的文件。"},
            {"id": "link", "label": "链接",
             "hint": "课板里放一个指向原文件的符号链接。适合已经整理好、不想再动的目录。"},
        ],
        "api_key_set": bool(cfg.api_key),
        "api_key_hint": _mask(cfg.api_key),
        "providers": [
            {
                "id": key,
                "label": spec.get("label", key),
                "base_url": spec["base_url"],
                "model": spec["model"],
                "ask_model": spec["ask_model"],
                "keyless": bool(spec.get("keyless")),
            }
            for key, spec in PROVIDER_DEFAULTS.items()
        ],
    }


def _current(field: str) -> str:
    """The stored value for a field, or "" when nothing is configured yet."""
    try:
        return str(getattr(load_config(), field, "") or "")
    except ConfigError:
        return ""


def _resolve(body: SettingsRequest) -> dict:
    """Fill in a provider's defaults, and keep the stored key when unchanged."""
    spec = PROVIDER_DEFAULTS.get(body.provider)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"未知的服务商 {body.provider!r}。")

    key = body.api_key
    if key == _KEEP_KEY or not key:
        try:
            key = load_config().api_key
        except ConfigError:
            key = ""

    # An omitted field means "leave it alone" -- never "reset to the provider
    # default". Getting this wrong silently rewrote settings the page had not
    # even mentioned: saving a language change also reset both model names.
    # Provider defaults are only a fallback for a field that has never been set,
    # and switching provider is what deliberately re-seeds them (the page sends
    # the new provider's values explicitly).
    switching = body.provider != _current("provider")

    def keep(field: str, sent: str) -> str:
        sent = sent.strip()
        if sent:
            return sent
        if not switching:
            stored = _current(field)
            if stored:
                return stored
        return str(spec.get(field, ""))

    values = {
        "provider": body.provider,
        "api_key": key,
        "base_url": keep("base_url", body.base_url),
        "model": keep("model", body.model),
        "ask_model": keep("ask_model", body.ask_model),
        "target_lang": body.target_lang.strip() or _current("target_lang") or "zh-CN",
        "user_data_path": str(
            Path(
                body.user_data_path.strip()
                or _current("user_data_path")
                or DEFAULT_USER_DATA
            ).expanduser()
        ),
        "import_mode": body.import_mode if body.import_mode in
                       {"move", "copy", "link"} else (_current("import_mode") or "move"),
        "window_mode": body.window_mode if body.window_mode in
                       {"fullscreen", "maximized"}
                       else (_current("window_mode") or "fullscreen"),
    }

    missing = [name for name in ("base_url", "model", "ask_model") if not values[name]]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="自定义服务商需要填写接口地址和两个模型名。",
        )
    if not spec.get("keyless"):
        if not values["api_key"]:
            raise HTTPException(status_code=400, detail="这个服务商需要 API key。")
        # A browser can autofill a password field with something unrelated, and
        # saving that silently destroys a working key. Nothing this short is a
        # real one.
        if len(values["api_key"]) < 8:
            raise HTTPException(
                status_code=400,
                detail="这个 API key 太短了，不像是真的。原来的设置没有改动。",
            )
    return values


@app.put("/api/settings")
def write_settings(body: SettingsRequest):
    values = _resolve(body)
    try:
        save_config(values)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"写入设置失败：{exc}") from exc

    # A changed location has to take effect immediately, or the board would
    # keep showing the old tree until a restart.
    _apply_paths(load_config())

    # Decks already open keep the provider they were created with. Say so
    # rather than letting a changed model appear not to take effect.
    return {"ok": True, "open_decks": len(store.all())}


@app.get("/api/settings/key")
def reveal_key():
    """The stored key in full, for the reveal control in the settings page.

    Only ever sent when asked for. The server is on loopback, serving the person
    whose key it is, but there is no reason to include it in the payload that
    renders the page.
    """
    try:
        return {"api_key": load_config().api_key}
    except ConfigError:
        return {"api_key": ""}


@app.post("/api/settings/models")
def list_models(body: SettingsRequest):
    """Ask the provider which models it actually has.

    Hardcoded model names go stale: the DeepSeek defaults shipped here named
    models the service had already replaced. The list belongs to the provider,
    so the settings page asks it rather than offering a guess.
    """
    import json
    import urllib.error
    import urllib.request

    from ..tls import create_context

    values = _resolve(body)
    request = urllib.request.Request(
        values["base_url"].rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {values['api_key']}"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=20, context=create_context()
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # Not every OpenAI-compatible service implements /models. Report it and
        # let the field stay free text rather than blocking the page.
        return {"models": [], "detail": f"取不到模型列表：{exc}"}

    listed = {
        str(entry.get("id"))
        for entry in payload.get("data", [])
        if isinstance(entry, dict) and entry.get("id")
    }
    # A provider's listing can omit a working endpoint: DeepSeek's leaves out
    # deepseek-chat, which is the one you actually want for translation.
    known = set(PROVIDER_DEFAULTS.get(values["provider"], {}).get("known_models", []))
    return {"models": sorted(listed | known), "detail": ""}


@app.post("/api/settings/test")
def test_settings(body: SettingsRequest):
    """Try the settings against the real service before committing to them."""
    from ..providers import build as build_provider

    values = _resolve(body)
    cfg = Config(
        provider=values["provider"], api_key=values["api_key"],
        base_url=values["base_url"], model=values["model"],
        ask_model=values["ask_model"], target_lang=values["target_lang"],
    )
    try:
        reply = build_provider(cfg, meter=False).complete(
            "Reply with the single word OK.",
            "ping",
            model=cfg.model,
            temperature=0,
            timeout=30.0,
        )
    except ProviderError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": f"连接成功，模型回了：{reply.strip()[:40]}"}


# -- what it has cost ------------------------------------------------------

@app.get("/api/spend")
def spend():
    """The running total, and enough of its basis to be checked."""
    from ..spend import ledger

    try:
        provider = load_config().provider
    except ConfigError:
        provider = ""
    data = ledger().totals(provider or None)
    data["by_model"] = ledger().by_model()
    data["daily"] = ledger().daily()
    return data


@app.post("/api/spend/prices")
def refresh_prices():
    """Fetch the price list now, rather than waiting for the weekly refresh."""
    from ..providers import build as build_provider
    from ..pricing import refresh
    from ..spend import ledger
    from ..translate import _lang

    cfg = _config()
    provider = build_provider(cfg)
    ok = refresh(
        ledger().table,
        cfg.provider,
        [cfg.model, cfg.ask_model],
        lambda system, user: provider.complete(
            system, user, model=cfg.model, json_mode=True, temperature=0,
            timeout=90.0, kind="pricing",
        ),
        cfg.ca_bundle or None,
        _lang(cfg.target_lang, cfg.target_lang),
    )
    if not ok:
        raise HTTPException(
            status_code=502,
            detail="没能从服务商的价目页读到价格。可以稍后再试，"
                   "或在 用户配置/pricing.json 里手填。",
        )
    ledger().reprice()
    data = ledger().totals(cfg.provider)
    data["by_model"] = ledger().by_model()
    data["daily"] = ledger().daily()
    return data


def _refresh_prices_quietly(session) -> None:
    """Piggyback the weekly price check on a question the user is already asking.

    They are waiting on the model anyway, so one extra small request costs them
    nothing they can feel -- and it is the only moment we can be sure there is a
    working key and a reachable service. Runs on a thread; failure is silent by
    design, because a stale price is not an error worth interrupting a lesson.
    """
    from ..pricing import ensure_fresh
    from ..spend import ledger
    from ..translate import _lang

    cfg = session.cfg

    def ask(system: str, user: str) -> str:
        return session.provider.complete(
            system, user, model=cfg.model, json_mode=True, temperature=0,
            timeout=90.0, kind="pricing",
        )

    ensure_fresh(
        ledger().table,
        cfg.provider,
        [cfg.model, cfg.ask_model],
        ask,
        cfg.ca_bundle or None,
        # Once a price is known, the calls made before it was is exactly the
        # backlog the user wants counted.
        after=lambda: ledger().reprice(),
        language=_lang(cfg.target_lang, cfg.target_lang),
    )


# -- status ----------------------------------------------------------------

@app.get("/api/status")
def status():
    try:
        cfg = load_config()
    except ConfigError as exc:
        return {"configured": False, "detail": str(exc)}
    return {
        "configured": True,
        "provider": cfg.provider,
        "model": cfg.model,
        "ask_model": cfg.ask_model,
        "target_lang": cfg.target_lang,
    }


# Attached here, after every endpoint above is defined and before the static
# mount below. The reader is served from "/", which matches everything, so any
# API route registered after it would be unreachable.
app.include_router(board_router)
app.include_router(glossary_router)


# -- the reader itself -----------------------------------------------------

if WEB_ROOT.exists():
    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")
else:
    @app.get("/")
    def missing_ui():
        return {"error": "The reader UI is not built. Run `npm run build` in web/."}

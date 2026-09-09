# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
.venv/bin/pytest                        # whole suite; no network, no API key
.venv/bin/pytest -k classify            # one area
.venv/bin/pytest tests/test_server.py::test_an_edit_survives_a_reload   # one test

.venv/bin/classhelper inspect FILE      # what was extracted, and why — no API calls
.venv/bin/classhelper inspect FILE --hidden --sentences   # + furniture, reasons, units

cd web && npm test                      # sync, selection and copy logic; no framework
cd desktop && npm run dev               # build the shell and run the app
cd desktop && npm run pack              # renderer, then sidecar, then the .app
cd desktop && npm run watch             # rebuild the UI on save, app already running
.venv/bin/python scripts/build_icon.py  # redraw the app icon into an .icns
```

`inspect` is the first thing to run when a deck reads badly: translation quality
is downstream of extraction quality, and it answers "did the text even come out
right" for free.

## Architecture

Everything hangs off one format-agnostic model in `model.py`: `Deck → Page →
TextBlock → Sentence`, with `BlockKind` deciding both how a block is displayed
and whether it is translated at all. A parser's only job is to produce a `Deck`;
nothing downstream knows which format it came from.

```
parsers/ ──→ classify.py ──→ segment.py ──→ translate.py ──→ server/ ──→ web/
 5 formats    what is this?   into units     page at a time   sessions    reader
                                             + glossary       + SSE
                                                  │
                          render.py ──────────────┼──→ pricing.py ──→ spend.py
                       source pages as            │    vendor rates    the ledger
                       pictures, for the          │    once a week     in yuan
                       side-by-side view          └──→ every call is metered
```

`library.py`'s `signature()` is how the reader notices someone else. The board
is a real directory tree, so the Finder is a second writer and the app cannot
assume it made every change. The client polls a sixteen-byte version derived
from a scan of the names the board shows -- not mtimes, or translating a deck
would move it constantly -- and refetches only when it differs. A poll rather
than a file watcher on purpose: a watcher that drops an event leaves the tree
wrong until the next restart with nothing to recover it, and this way there is
no platform-specific code at all.

`board.py` holds the persistent structure: folders (semester → course by
convention, nested freely) and the decks filed under them. It is deliberately
*not* a mirror of the filesystem — files stay where they are, the board records
where they belong. Imports land in the inbox until filed.

`glossary.py` layers on that. `ScopedGlossary` stacks one `Glossary` per scope
in the board chain (`global` → semester → course); the narrower one wins, and it
presents the same surface as a single `Glossary` so the translator neither knows
nor cares how many layers are underneath. A term the user locks is authoritative
and is fed into every later request; terms the model proposes only hold one deck
self-consistent, yield to the user silently, and are never written into a
narrower scope when a broader one already covers them.

`server/session.py` schedules translation by distance from the page being read,
re-evaluated whenever the reader scrolls, so the page on screen is always next.

`render.py` answers "can we show the file itself". A PDF: yes, PyMuPDF. A pptx:
only by borrowing LibreOffice, and only if it is installed -- otherwise the
reader draws the page from the geometry the parser already extracted and says
that is what it is doing. Nothing here blocks: a conversion runs on a thread and
the approximation stands in until it lands.

`pricing.py` and `spend.py` are the money. Prices are *fetched* from the
vendor's own pricing page and read out of it by the model -- never recalled from
its training -- then bounds-checked, so a misread table is dropped rather than
believed. `spend.py` records every call with the rates it was billed at, and a
model with no published price is reported separately instead of counted as free.
The two panes' scroll sync lives in `web/src/sync.ts`; the panes negotiate a
height per page there, which is the only reason a page boundary lines up.

Providers (`providers/`) are one interface with `complete()` and `stream()`.
Adding a vendor is one class plus one registry line; no prompt construction may
depend on vendor-specific features.

## Shape of the thing

`desktop/` is an Electron shell whose renderer root points at `../web` — the UI
lives there and is built from that one source. The main process spawns the
Python server (`classhelper serve --port N`) as a child and loads its localhost
URL.

Python keeps the document work deliberately: the pptx workarounds below were
found on real decks, and JavaScript has no equivalent of python-pptx to start
from. Rewriting them in Node would mean rediscovering the same bugs.

`web/src/platform.ts` is the only place that touches the preload bridge. Note
`pathsFromDrop`: a web page is told a dropped file's contents but never its
path, so without the bridge a drop can only be uploaded as bytes and the import
mode cannot apply to it.

## Things that will bite you

**python-pptx silently loses text, twice over.** It does not descend into
`mc:AlternateContent`, where PowerPoint puts any shape using a post-2010
feature — often the main body placeholder. And `paragraph.text` skips OMML
(`<m:oMath>`), so equations vanish and a sentence arrives with a hole in it.
`pptx_parser.py` works around both; do not "simplify" it back to `.text`.

**PyMuPDF returns one block per line as often as one per paragraph**, depending
on how the PDF was produced, and the three cases need opposite treatment:
prose must be rejoined (or sentences are cut mid-clause), a bulleted list must
not be (or five bullets become one run-on sentence), and a code listing must
keep its newlines *and* its indentation (or a seven-line program becomes one
unreadable line). `_block_segments` and `_absorb` decide between them; the
monospace flag is what distinguishes the third.

**Blocking calls in async handlers.** `server/app.py`'s event stream waits on a
thread-fed `queue.Queue`. Awaiting that inline pins the event loop for its whole
timeout and starves every other request in the process — it presents as a slow
model, not as a server bug. Use `asyncio.to_thread`.

**Events emitted before a client subscribes are lost.** A fully cached deck
finishes translating in milliseconds, long before the browser opens its
connection, so the stream sends a full snapshot on connect. Subscribe *before*
snapshotting, never after.

**The price table is a file the user may edit.** `Table` re-reads it when the
mtime changes; read once at startup, a hand-corrected price would not take
effect until the next launch.

**A course folder is not only decks.** The board lists every file in it -- a
syllabus, a dataset, a zip -- because it is a real directory and hiding things
would make it disagree with the Finder. `Item.readable` says whether the reader
can open one; filing, moving and rescuing on delete all work regardless.

**A document has no pages, so the parser invents them.** Markdown splits on a
thematic break if the file uses them and on top-level headings otherwise; a
.docx splits on explicit page breaks, else on Heading 1. Neither invents a
break in continuous prose -- one long page is the honest answer.

**python-docx drops OMML too**, exactly as python-pptx does, so `docx_parser`
walks the XML in document order for the same reason `pptx_parser` does. A
handout whose formulae have silently vanished is worse than one that fails to
open.

**`.ppt` is a compound binary, not a zip.** Nothing in Python reads it, so it
is converted once by LibreOffice and read as .pptx. Without LibreOffice the
error names the install and the alternative rather than saying "unsupported".

**A format that records no positions has no approximation to fall back on.** A
.docx knows its text and nothing about where it sat, so the slide pane reports
`none` rather than drawing a picture of nothing; a .pptx, which does record
boxes, still falls back to the drawing.

**Nothing outside the app can be assumed not to have happened.** The board is
a directory tree people rearrange in the Finder, which is the point of it being
one. Loading it once and refreshing only on the app's own edits leaves the
reader confidently showing a tree that is gone.

**The board page shows one level.** Rendering children recursively listed the
same deck twice -- under its course and again under the semester -- and padded
the page with "and 1 more level" lines. Navigating into a folder is a click.

**Dockview fixes a panel's component when the panel is created.** A value
threaded to a panel through the `components` map is frozen at whatever it was
then -- this is why the theme lives in the store: as a prop it changed
everywhere except in the control that changed it, which read as a dead switch.
Anything a panel must see live belongs in the store.

**Every provider is the same code.** They all speak OpenAI chat-completions, so
`providers/` has one implementation and `PROVIDER_DEFAULTS` in `config.py` is
the list. Adding a service is a dict entry, not a class.

**An omitted settings field means "leave it alone", never "reset to default".**
This bit twice: a save that did not mention `library_path` relocated the whole
library, and one that mentioned only the language rewrote both model names back
to the provider defaults -- which is what "my settings never save" turned out to
be. `_current()` and `keep()` in `app.py` hold every unmentioned field where it
was; provider defaults re-seed only when the provider itself changes.

**A streamed reply reports no usage unless you ask for it.** OpenAI-compatible
`stream: true` omits the `usage` object entirely without
`stream_options.include_usage`, so every question the user asked was billed to
them and counted as free.

**`prompt_tokens` already contains the cache hits.** Adding
`prompt_cache_hit_tokens` to it bills the cached half twice -- at thirty times
its real price, on DeepSeek. `Call.uncached` subtracts.

**Two scrollers cannot be synced by copying scrollTop.** They are never the same
height: one page's translation runs long, the next one's is a heading. Position
is a page plus a fraction of the way down it, which both panes can act on
whatever height each gives that page (`web/src/sync.ts`).

**Following a pane writes the other one, and that write fires a scroll event.**
Mistake it for a real one and the handler turns around and writes back into the
pane under the user's hand -- once per frame, which on a trackpad cancels the
momentum every frame. It does not read as a sync bug; it reads as the app
stuttering, and that is what it was reported as. The echo is recognised by
*position*: `apply()` records what it wrote and `isOurs()` compares, so our own
event is ignored while a genuine scroll of that pane still takes effect at once.
A time-based mute would do neither well. `sync.test.ts` counts writes rather
than checking the final position -- a write-back usually lands on the position
the pane was already at, so it is invisible in the value and perfectly visible
on a trackpad.

**Reading `offsetTop` for every page on every scroll event is a forced layout
per page per frame**, and a deck is routinely over a hundred pages. `offsets()`
caches, keyed on the scroller's `scrollHeight` -- one cheap read that changes
whenever any page's height does.

**The reader told the server which page it was on once per observer callback.**
A flick down a long deck sent fifty HTTP posts to reorder a queue fifty times
and arrive at the answer the last one gave. It is debounced, and skipped
entirely when the page has not changed.

**A synced pane must carry no padding of its own.** The panes agree on a height
per page and each applies it as a `min-height`; if the section also has padding,
a border or a margin, the two sides end up that much apart and the gap
accumulates down the deck. All the spacing lives inside `.page-inner` and
`.source-inner`, which are also the elements that get measured -- measuring the
section instead would feed its own minimum back in and the panes would grow
without bound.

**A slide's bullets are one shape and many blocks.** They all carry the shape's
position, so the drawn fallback has to group by `shape` and stack them inside
one box; positioned individually they land on top of each other and a slide
renders as a smear.

**A sentence's id is a hash of its text, so it cannot key a selection.** A
definition restated on three slides is one id in three places -- select one and
all three light up. `web/src/units.ts` keys on position instead, and the id is
used only when talking to the server, where the text is all that matters.

**The order sentences are selected in is not the order they are shown in.** The
reader puts the reading column first and gathers the diagram labels underneath,
so a caption the parser found halfway down the slide is read last. `units()`
lists flow blocks then figure blocks per page, because a shift-click has to
sweep what lies between two points *on screen*.

**A menu that opens on a right-click must not listen for one yet.** React
treats contextmenu as discrete input and can flush the mounting effect while
that same event is still travelling to `window`, so the click that opened the
menu closes it and nothing appears. `CopyMenu` arms its dismiss listeners a
tick late.

**Two components reading the same setting from localStorage hold two
settings.** The context picker moved and the question kept sending the old
value. Same rule as the theme: anything two panels must agree on lives in the
store.

**A lazily loaded image with no reserved space moves everything below it.**
Clicking a chapter computes where that page starts, and by the time the smooth
scroll got there a diagram had loaded and pushed the page hundreds of pixels
further down -- so the jump landed short, and it looked like a sync bug. The
reader sets an `aspect-ratio` on every figure from the box the parser recorded,
so the space is there before the bytes are.

**A smooth scroll outlasts a fixed mute.** Muting the other pane for 900ms was
fine for a short jump and wrong for a long one: past the window the two panes
began correcting each other mid-animation, and writing scrollTop cancels a
smooth scroll. The mute now ends on *arrival* at the recorded target, with a
ceiling only as a backstop.

**The slide pane is built from nothing every time you switch to it.** Left to
itself it mounts at the top while the reader is halfway down the deck, so
`Group.adopt` brings a newly attached pane to where the others already are.

**A "have we loaded yet" ref does not gate an autosave.** The ref was set the
moment the fetch resolved -- before React rendered the state that same callback
had just set -- so the first render holding a form already counted as a change,
and a fresh install wrote its own defaults and came up reporting itself
configured. `Settings.tsx` compares the form against `stored`, a signature of
what was loaded or last saved: no difference, no write, whatever order the
renders arrive in.

**An unsigned bundle is reported as *damaged*, not as unsigned.** Injecting the
Python server into `Contents/Resources` invalidates the signature Electron
ships with, and macOS refuses a broken seal with a dialog that has no way past
it. `identity: "-"` in electron-builder re-signs the finished bundle ad hoc,
which puts it back to being merely unidentified -- something the user can
allow. Nothing here can be notarised without a paid Developer ID.

**The icon is code, not an asset.** `scripts/build_icon.py` draws it at every
size macOS asks for rather than scaling one bitmap down. Full bleed on purpose:
macOS applies its own rounded mask, so artwork with corners of its own is
rounded twice. An adaptive light/dark icon would need Icon Composer and
`actool`, which require full Xcode.

**Build the renderer before the sidecar.** `build_sidecar.py` freezes
`src/classhelper/web` into the binary, so `npm run pack` running them the other
way round shipped whichever assets happened to be lying in that directory.

**Three locations, deliberately separate.** `config.toml` sits beside
`ClassHelper.command` (`_project_root()` in `config.py`), because this is a
program you keep in a folder. Everything the user owns is under one configured
directory as `课板/` (the library) and `用户配置/` (caches, fallbacks) -- one path
to point at a synced folder. Modules reach the latter through
`config.state_dir()`, never a module-level constant, so it can follow the
setting.

**Importing never opens.** `/api/upload` and `/api/import` put files on the
board's inbox and return the board; opening is a separate, explicit act. Wiring
them back together buries the window in reader tabs on a single folder drop.

**Tests must patch `board_api._store`.** It binds its path at import time, so
patching `CONFIG_DIR` alone is not enough -- without it the suite writes into the
developer's real board. The `client` fixture in `test_server.py` does this.

**assistant-ui keeps a thread inside its runtime hook**, so a conversation lives
exactly as long as the component holding it. The ask panel therefore mounts one
thread per open deck and *hides* the inactive ones. Swapping the panel's
contents on deck change would both discard the conversation and show one deck's
answers under another's slides.

**The sidebar is not a Dockview panel.** A docked group shares the window
proportionally, so the explorer grew to half the screen whenever a panel on the
right was closed. It lives in `.workspace` beside the Dockview root, with its
own tab strip and resizer -- the same arrangement an editor uses, and the only
one where a fixed width stays fixed.

**Dockview panels must anchor to the reading area, never to "the active
panel".** Side panels added while no deck is open become the main area, and the
readers that arrive later get squeezed into a sliver. `readerPosition` and
`sidePosition` in `App.tsx` exist for this; the side panels are also laid out
only once a deck exists. And any panel a user can close needs a way back — the
视图 menu — or the app becomes unusable with one stray click.

**The cache is keyed on sentence text, target language and model**, so anything
that should change a translation must invalidate it first (`lock_term` does; a
change that forgets to will appear to do nothing at all) -- and conversely,
changing the model silently invalidates everything. That is correct, and it is
also a bill, so the settings page warns before saving such a change.

## Two rules the code is built around

**When classification evidence is weak, return `BODY`.** A stray line of
navigation noise is visible and ignorable; a hidden line of the lecture is a
silent failure the user cannot detect. Rules only move a block out of the
reading flow on positive evidence, `_rescue_page` guarantees no page is left
empty, and every decision records a `kind_reason` so a bad call on an unfamiliar
deck is diagnosable. Structural facts from the file (placeholder types) outrank
our inferences and are never overturned by the rescue.

**Never invent a translation.** Units are numbered on the way out and verified
on return; missing ones are re-requested, then marked `flagged` and reported as
failed. A visible gap the user can retry is recoverable, a plausible sentence
nobody asked for is not.

## Conventions

Nothing hardcodes a language pair or a vendor — `target_lang` and `provider` are
configuration, and the English→Chinese defaults are only defaults.

Tests generate their own `.pptx` fixtures and never call the network. Course
material is gitignored; do not commit slides.

Secrets live in `~/.classhelper/config.toml`, never in the project directory.

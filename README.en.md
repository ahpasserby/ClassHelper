<div align="center">

# ClassHelper

**Read English lecture slides with a Chinese translation under every sentence, and ask an AI about anything you do not follow.**

[![Source](https://img.shields.io/badge/Source-GitHub-8A2BE2?style=for-the-badge&logo=github&logoColor=white&labelColor=2b2b2b)](https://github.com/ahpasserby/ClassHelper)
[![License](https://img.shields.io/badge/License-MIT-c0392b?style=for-the-badge&logo=opensourceinitiative&logoColor=white&labelColor=2b2b2b)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-macOS%20Apple%20Silicon-0a84ff?style=for-the-badge&logo=apple&logoColor=white&labelColor=2b2b2b)](https://github.com/ahpasserby/ClassHelper/releases)
[![Download](https://img.shields.io/badge/Download-Release-2ea44f?style=for-the-badge&logo=github&logoColor=white&labelColor=2b2b2b)](https://github.com/ahpasserby/ClassHelper/releases/latest)

[简体中文](README.md) | English

</div>

---

Takes `.pptx` and `.pdf`. Each sentence gets its translation directly
underneath, so the two always line up.

The target language is a setting: English to Chinese is only the default.

![ClassHelper](docs/screenshot-light.png)

## What it does

- **Sentence by sentence.** The whole page is used as context, so terminology
  stays consistent across the deck. Code, equations and proper nouns are left
  alone.
- **The slide beside the text.** Source page on the left, translation on the
  right, scrolling in step with the pages lined up.
- **Ask about a selection.** Pick one sentence or several; the question carries
  the page in both languages and the course glossary with it.
- **Three ways to copy.** Right-click a selection for the source, the
  translation, or both paired.
- **Glossary.** Lock a term and every sentence using it is retranslated. Scoped
  globally, per semester, or per course.
- **Board.** Semesters and courses as real directories. Rearrange them in the
  Finder and the app notices within two seconds.
- **Cost.** A running total in yuan in the status bar, priced from the
  provider's own published rates.
- **Cache.** Translations live beside the deck, so reopening it is instant and
  free.

<div align="center">
<img src="docs/screenshot-dark.png" width="49%" />
<img src="docs/screenshot-board.png" width="49%" />
</div>

## Install

Download `ClassHelper-*-mac-arm64.dmg` from
[Releases](https://github.com/ahpasserby/ClassHelper/releases/latest) and drag
the app into Applications.

macOS (Apple Silicon) only for now. The app is not signed by Apple, so the
first launch is blocked: **right-click the icon in Applications, choose Open,
then Open again in the dialog.** Or from a terminal:

```bash
xattr -dr com.apple.quarantine /Applications/ClassHelper.app
```

## Setup

Open Settings on first launch and enter an API key. DeepSeek is the default
because translating a whole deck costs a few cents.

| Setting | Notes |
| --- | --- |
| Provider | DeepSeek, OpenAI, Moonshot, SiliconFlow, Ollama, or any OpenAI-compatible endpoint |
| Translation model | Called a lot; use a cheap one |
| Ask model | One call at a time; a reasoning model is worth it |
| Target language | `zh-CN` by default; `en`, `ja` and so on all work |
| Data directory | Decks, glossaries and caches all live under it |

For real slide images from `.pptx`, install LibreOffice and the app will borrow
it:

```bash
brew install --cask libreoffice
```

Without it you get a reconstruction drawn from the extracted layout, and the
panel says so.

## Privacy

Slide text is sent to whichever provider you configure. Think before pointing
it at anything confidential, or switch the provider to a local Ollama.

The API key is stored in
`~/Library/Application Support/classhelper-desktop/config.toml`.

## Development

Needs Python 3.11+, Node 20+ and macOS.

```bash
git clone git@github.com:ahpasserby/ClassHelper.git
cd ClassHelper

python3 -m venv .venv && .venv/bin/pip install -e .
cd web && npm install && cd ..
cd desktop && npm install

npm run dev     # run it
npm run dist    # build a dmg into desktop/dist/
```

Tests:

```bash
.venv/bin/pytest        # backend; no network, no API key
cd web && npm test      # frontend
```

To see what was extracted from a deck, without spending anything:

```bash
.venv/bin/classhelper inspect slides.pptx --hidden --sentences
```

## Layout

| Path | What lives there |
| --- | --- |
| `model.py` | The format-agnostic document model |
| `parsers/` | `.pptx` and `.pdf` readers |
| `classify.py` | Deciding what each block is |
| `segment.py` | Splitting text into translation units |
| `translate.py` | Page-at-a-time translation and alignment checks |
| `glossary.py` | Terminology, scoped and inherited |
| `library.py` | The board as a directory tree |
| `render.py` | Pictures of the source pages |
| `pricing.py` | Reading the provider's published rates |
| `spend.py` | The ledger |
| `ask.py` | Building the context a question needs |
| `providers/` | Model backends |
| `server/` | Local HTTP API and open-deck sessions |
| `web/` | The reader UI |
| `desktop/` | Electron shell and packaging |

Adding an input format means one `parse(path) -> Deck`. Adding a provider means
one class with `complete()` and `stream()`. Nothing downstream changes.

## Two rules the code follows

**When the evidence is weak, treat it as body text.** A stray header you can
see and ignore; a hidden line of the lecture you never find out about. Content
is only folded away on positive evidence.

**Never invent a translation.** Units are numbered on the way out and checked
on return; missing ones are re-requested, then flagged and reported. A visible
gap you can retry beats a plausible sentence nobody asked for.

## License

[MIT](LICENSE)

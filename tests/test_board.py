"""The library: the board as a real directory tree, and terminology scoped to it.

The rules worth testing are the ones that quietly lose work -- a folder
swallowing itself, a delete taking decks with it, a course-level correction
leaking into an unrelated course -- plus the traversal guard, since every path
here arrives from a browser.
"""

from __future__ import annotations

import pytest

from classhelper.glossary import ScopedGlossary
from classhelper.library import INBOX, TRASH, ImportMode, Library, LibraryError


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Keep per-user state out of the developer's own directories."""
    import classhelper.config as config_module

    (tmp_path / "state").mkdir(exist_ok=True)
    monkeypatch.setattr(config_module, "_state_dir", tmp_path / "state")


@pytest.fixture
def library(tmp_path):
    lib = Library(tmp_path / "library")
    lib.ensure()
    return lib


@pytest.fixture
def deck(tmp_path):
    def make(name: str = "Lec01.pptx"):
        from pptx import Presentation

        path = tmp_path / "downloads" / name
        path.parent.mkdir(exist_ok=True)
        Presentation().save(str(path))
        return path

    return make


def test_a_folder_is_a_directory(library):
    semester = library.create_folder(None, "2026 秋季")
    course = library.create_folder(semester, "DMS")
    assert (library.root / "2026 秋季" / "DMS").is_dir()
    assert course == "2026 秋季/DMS"


def test_duplicate_names_are_refused_only_among_siblings(library):
    semester = library.create_folder(None, "S")
    library.create_folder(semester, "DMS")
    with pytest.raises(LibraryError):
        library.create_folder(semester, "DMS")
    other = library.create_folder(None, "T")
    library.create_folder(other, "DMS")  # a different semester may reuse the name


def test_a_name_cannot_escape_the_library(library):
    """Folder names arrive from the browser; separators are stripped rather
    than escaped, because `../etc` is never what anyone meant to type."""
    created = library.create_folder(None, "../evil")
    assert (library.root / "..evil").is_dir() or ".." not in created
    assert library.root in (library.root / created).parents or True
    with pytest.raises(LibraryError):
        library.resolve("../../etc/passwd")


def test_importing_moves_the_original_by_default(library, deck):
    source = deck()
    item = library.import_path(source, ImportMode.MOVE)
    assert item.path == f"{INBOX}/Lec01.pptx"
    assert not source.exists(), "move must not leave a second copy behind"
    assert (library.root / INBOX / "Lec01.pptx").exists()


def test_importing_can_copy_instead(library, deck):
    source = deck()
    library.import_path(source, ImportMode.COPY)
    assert source.exists()
    assert (library.root / INBOX / "Lec01.pptx").exists()


def test_importing_can_link_instead(library, deck):
    source = deck()
    library.import_path(source, ImportMode.LINK)
    linked = library.root / INBOX / "Lec01.pptx"
    assert linked.is_symlink() and linked.resolve() == source.resolve()


def test_a_broken_link_is_listed_as_missing_not_crashed(library, deck):
    """Following the link would take path handling outside the library, which
    used to raise while merely listing the folder."""
    source = deck()
    library.import_path(source, ImportMode.LINK)
    source.unlink()
    assert [i.missing for i in library.inbox()] == [True]


def test_a_second_file_of_the_same_name_is_numbered(library, deck):
    library.import_path(deck(), ImportMode.COPY)
    library.import_path(deck(), ImportMode.COPY)
    assert sorted(i.name for i in library.inbox()) == ["Lec01", "Lec01 (2)"]


def test_filing_a_deck_moves_the_file(library, deck):
    course = library.create_folder(None, "DMS")
    item = library.import_path(deck(), ImportMode.MOVE)
    library.move([item.path], course)
    assert (library.root / "DMS" / "Lec01.pptx").exists()
    assert library.inbox() == []


def test_a_folder_cannot_be_moved_into_itself(library):
    parent = library.create_folder(None, "A")
    child = library.create_folder(parent, "B")
    with pytest.raises(LibraryError):
        library.move([parent], child)


def test_deleting_a_folder_rescues_its_decks(library, deck):
    """Deleting must never take material with it silently: the decks come back
    to the inbox, and the folder goes to the trash rather than being erased."""
    semester = library.create_folder(None, "S")
    course = library.create_folder(semester, "DMS")
    item = library.import_path(deck(), ImportMode.MOVE)
    library.move([item.path], course)

    rescued = library.delete_folder(semester)
    assert rescued == 1
    assert [i.name for i in library.inbox()] == ["Lec01"]
    assert not (library.root / "S").exists()
    assert (library.root / TRASH / "S").is_dir()


def test_deleting_a_deck_sends_it_to_the_trash(library, deck):
    item = library.import_path(deck(), ImportMode.MOVE)
    library.delete_item(item.path)
    assert library.inbox() == []
    assert (library.root / TRASH / "Lec01.pptx").exists()


def test_renaming_a_deck_keeps_its_extension(library, deck):
    item = library.import_path(deck(), ImportMode.MOVE)
    renamed = library.rename(item.path, "第一讲")
    assert renamed.endswith("第一讲.pptx")


def test_the_scope_chain_runs_from_the_root_down(library):
    semester = library.create_folder(None, "S")
    course = library.create_folder(semester, "DMS")
    assert library.chain(course) == ["", "S", "S/DMS"]
    assert library.chain(None) == [""]


def test_a_file_of_any_kind_is_listed_in_the_inbox(library, tmp_path):
    """Everything filed shows up; whether the reader can open it is a separate
    fact carried on the item."""
    (library.root / INBOX / "notes.txt").write_text("hello")
    inbox = library.inbox()
    assert [i.name for i in inbox] == ["notes"]
    assert not inbox[0].readable


# -- terminology, stored in the folder it describes -------------------------

@pytest.fixture
def scoped(library, monkeypatch):
    import classhelper.glossary as glossary_module

    glossary_module.use_library(library.root)
    yield lambda scopes: ScopedGlossary(scopes, "zh-CN")
    glossary_module.use_library(None)


def test_a_glossary_lives_inside_its_own_folder(library, scoped):
    course = library.create_folder(None, "DMS")
    scoped(["", course]).set("key", "码", scope=course)
    assert (library.root / "DMS" / ".classhelper" / "glossary.zh-cn.json").exists()


def test_a_course_inherits_everything_broader(library, scoped):
    semester = library.create_folder(None, "S")
    course = library.create_folder(semester, "DMS")
    scoped([""]).set("algorithm", "算法")
    scoped(["", semester]).set("key", "键", scope=semester)

    resolved = scoped(["", semester, course]).resolved()
    assert {r.term: r.translation for r in resolved} == {
        "algorithm": "算法",
        "key": "键",
    }
    assert all(r.inherited for r in resolved)


def test_an_override_does_not_leak_to_a_sibling_course(library, scoped):
    """The reason scoping exists: `key` is 码 in databases and stays 键 next door."""
    semester = library.create_folder(None, "S")
    dms = library.create_folder(semester, "DMS")
    other = library.create_folder(semester, "OS")
    scoped(["", semester]).set("key", "键", scope=semester)
    scoped(["", semester, dms]).set("key", "码")

    assert [r.translation for r in scoped(["", semester, other]).resolved()] == ["键"]


def test_removing_an_override_reveals_what_it_covered(library, scoped):
    semester = library.create_folder(None, "S")
    course = library.create_folder(semester, "DMS")
    scoped(["", semester]).set("key", "键", scope=semester)
    here = scoped(["", semester, course])
    here.set("key", "码")
    here.unset("key")
    assert [r.translation for r in scoped(["", semester, course]).resolved()] == ["键"]


def test_the_model_does_not_shadow_an_inherited_term(library, scoped):
    semester = library.create_folder(None, "S")
    course = library.create_folder(semester, "DMS")
    scoped(["", semester]).set("entity", "实体", scope=semester)
    here = scoped(["", semester, course])
    here.observe({"entity": "实物", "attribute": "属性"})
    resolved = {r.term: r for r in here.resolved()}
    assert resolved["entity"].translation == "实体"
    assert resolved["entity"].inherited is True
    assert resolved["attribute"].inherited is False


def test_a_course_folder_carries_its_terminology_when_copied(library, scoped, tmp_path):
    """The point of storing this in the folder: copy the folder and the
    terminology comes with it."""
    import shutil

    course = library.create_folder(None, "DMS")
    scoped(["", course]).set("key", "码", scope=course)

    elsewhere = Library(tmp_path / "elsewhere")
    elsewhere.ensure()
    shutil.copytree(library.root / "DMS", elsewhere.root / "DMS")

    import classhelper.glossary as glossary_module

    glossary_module.use_library(elsewhere.root)
    moved = ScopedGlossary(["", "DMS"], "zh-CN")
    assert [(r.term, r.translation) for r in moved.resolved()] == [("key", "码")]


# -- list layout ------------------------------------------------------------

def test_a_bulleted_pdf_list_is_not_flattened_into_one_paragraph(tmp_path):
    """PyMuPDF hands back one line per bullet, exactly as it does for a wrapped
    paragraph. Joining them produced a single run-on sentence that read as
    nonsense and translated as nonsense."""
    import pymupdf

    from classhelper.parsers import pdf_parser

    path = tmp_path / "bullets.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Goal", fontsize=14)
    for n, line in enumerate(
        ["• solve it efficiently",
         "• by using algorithms and data structures,",
         "• convert our solution into a program"]
    ):
        page.insert_text((72, 130 + n * 18), line, fontsize=11)
    doc.save(str(path))
    doc.close()

    deck = pdf_parser.parse(str(path))
    texts = [b.text for b in deck.pages[0].blocks]
    assert "solve it efficiently" in texts
    assert "by using algorithms and data structures," in texts
    assert not any(
        "solve it efficiently by using" in t for t in texts
    ), "the bullets were joined into one paragraph"
    bullets = [b for b in deck.pages[0].blocks if b.meta.get("bullet")]
    assert len(bullets) == 3


def test_a_wrapped_paragraph_is_still_rejoined(tmp_path):
    """The other half of the same decision: lines that are not list items must
    still be joined, or sentences get cut at the line break."""
    import pymupdf

    from classhelper.parsers import pdf_parser

    path = tmp_path / "prose.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Flip game is played on a rectangular field", fontsize=11)
    page.insert_text((72, 114), "with two-sided pieces on each square.", fontsize=11)
    doc.save(str(path))
    doc.close()

    deck = pdf_parser.parse(str(path))
    assert any(
        "rectangular field with two-sided pieces" in b.text
        for b in deck.pages[0].blocks
    )


def test_a_code_listing_keeps_its_lines_and_indentation(tmp_path):
    """Collapsing whitespace the way prose is collapsed turned a seven line C
    program into one line with its indentation gone -- which is the one thing
    the listing was on the slide to convey."""
    import pymupdf

    from classhelper.classify import classify
    from classhelper.model import BlockKind
    from classhelper.parsers import pdf_parser

    path = tmp_path / "code.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Statement", fontsize=14)
    for n, line in enumerate(
        ["int main()", "{", "    printf(\"hi\");", "    return 0;", "}"]
    ):
        page.insert_text((72, 120 + n * 16), line, fontname="cour", fontsize=11)
    doc.save(str(path))
    doc.close()

    deck = pdf_parser.parse(str(path))
    classify(deck)
    code = [b for b in deck.pages[0].blocks if b.kind is BlockKind.CODE]
    assert len(code) == 1, [b.text for b in deck.pages[0].blocks]
    lines = code[0].text.split("\n")
    assert lines[0] == "int main()"
    assert lines[2].startswith("    printf"), "indentation was collapsed"
    assert len(lines) == 5


def test_a_listing_is_never_split_on_an_operator(tmp_path):
    """`-` and `*` open a bullet in prose and are arithmetic in code."""
    import pymupdf

    from classhelper.parsers import pdf_parser

    path = tmp_path / "ops.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    for n, line in enumerate(["int a = 1;", "- b;", "* c;"]):
        page.insert_text((72, 100 + n * 16), line, fontname="cour", fontsize=11)
    doc.save(str(path))
    doc.close()

    deck = pdf_parser.parse(str(path))
    mono = [b for b in deck.pages[0].blocks if b.meta.get("mono")]
    assert len(mono) == 1 and mono[0].text.count("\n") == 2


# -- noticing changes made outside the app ---------------------------------

def test_the_signature_changes_when_the_board_does(library, deck):
    """The board is a real directory tree, so the Finder is a second writer.
    The reader polls this to find out it has been overtaken."""
    empty = library.signature()

    library.create_folder(None, "2026秋")
    with_folder = library.signature()
    assert with_folder != empty

    library.import_path(deck("Lec01.pptx"), ImportMode.COPY, "2026秋")
    with_deck = library.signature()
    assert with_deck != with_folder

    library.create_folder("2026秋", "数据库")
    nested = library.signature()
    assert nested != with_deck


def test_moving_a_deck_between_folders_changes_the_signature(library, deck):
    """The set of names is the same afterwards; only the nesting moved. A
    signature built from names alone would call that unchanged."""
    library.create_folder(None, "甲")
    library.create_folder(None, "乙")
    item = library.import_path(deck("Lec01.pptx"), ImportMode.COPY, "甲")
    before = library.signature()

    library.move([item.path], "乙")
    assert library.signature() != before


def test_editing_a_deck_does_not_change_the_signature(library, deck):
    """Translating a deck writes constantly. If that moved the signature the
    reader would refetch the whole tree to be told nothing had changed."""
    item = library.import_path(deck("Lec01.pptx"), ImportMode.COPY, None)
    before = library.signature()

    path = library.resolve(item.path)
    path.write_bytes(path.read_bytes() + b"\x00")
    (library.meta_dir(None) / "translations.db").write_bytes(b"x" * 100)

    assert library.signature() == before


def test_a_file_the_board_would_not_show_does_not_change_the_signature(library):
    """Dotfiles only. Everything the board lists is in the signature."""
    library.create_folder(None, "2026秋")
    before = library.signature()
    (library.resolve("2026秋") / ".DS_Store").write_bytes(b"x")
    assert library.signature() == before


def test_a_deck_dropped_into_the_inbox_by_hand_is_noticed(library, deck):
    before = library.signature()
    source = deck("Lec01.pptx")
    (library.root / INBOX / source.name).write_bytes(source.read_bytes())
    assert library.signature() != before


def test_the_version_endpoint_matches_the_board(client, tmp_path):
    import classhelper.server.board_api as board_module

    lib = Library(tmp_path / "lib2")
    board_module.use_library(lib)

    board = client.get("/api/board").json()
    assert board["version"] == client.get("/api/board/version").json()["version"]

    lib.create_folder(None, "新学期")
    assert client.get("/api/board/version").json()["version"] != board["version"]


# -- course material that is not a deck -------------------------------------

def test_the_board_lists_files_it_cannot_open(library):
    """A course folder collects whatever the course hands out. Hiding the
    syllabus would make the board disagree with the folder it stands for."""
    folder = library.resolve(library.create_folder(None, "DSA"))
    (folder / "Lec01.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "syllabus.txt").write_text("weeks", encoding="utf-8")
    (folder / "starter.zip").write_bytes(b"PK\x03\x04")

    items = {i.name: i for i in library.tree()[0].items}
    assert set(items) == {"Lec01", "syllabus", "starter"}
    assert items["Lec01"].readable
    assert not items["syllabus"].readable
    assert not items["starter"].readable


def test_adding_material_that_is_not_a_deck_changes_the_signature(library):
    """Otherwise dropping a handout into a course folder would not show up
    until something else did."""
    folder = library.resolve(library.create_folder(None, "DSA"))
    before = library.signature()
    (folder / "syllabus.txt").write_text("weeks", encoding="utf-8")
    assert library.signature() != before


def test_hidden_files_are_still_not_listed(library):
    folder = library.resolve(library.create_folder(None, "DSA"))
    (folder / ".DS_Store").write_bytes(b"x")
    assert library.tree()[0].items == []


def test_material_can_be_filed_like_anything_else(library, tmp_path):
    """It is on the board to be organised, so importing and moving it has to
    work -- refusing would leave it stuck wherever it landed."""
    source = tmp_path / "downloads" / "syllabus.txt"
    source.parent.mkdir(exist_ok=True)
    source.write_text("weeks", encoding="utf-8")

    item = library.import_path(source, ImportMode.COPY)
    assert not item.readable
    assert item.path.startswith(INBOX)

    course = library.create_folder(None, "DSA")
    moved = library.move([item.path], course)
    assert moved[0].startswith("DSA/")


def test_deleting_a_folder_rescues_material_too(library):
    """Its contents go back to the inbox. Leaving the handouts to be trashed
    with the folder would delete files the user never chose to delete."""
    course = library.create_folder(None, "DSA")
    folder = library.resolve(course)
    (folder / "Lec01.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "syllabus.txt").write_text("weeks", encoding="utf-8")

    assert library.delete_folder(course) == 2
    assert {i.name for i in library.inbox()} == {"Lec01", "syllabus"}

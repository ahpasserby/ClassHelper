"""Sentence splitting. Every case here is one the naive `text.split(". ")`
gets wrong, which is why the module exists."""

from classhelper.segment import segment


def test_keeps_abbreviation_with_its_sentence():
    assert segment("E.g. David and Goliath are both students.") == [
        "E.g. David and Goliath are both students."
    ]


def test_splits_two_plain_sentences():
    assert segment("We compare the values. They are different entities.") == [
        "We compare the values.",
        "They are different entities.",
    ]


def test_does_not_split_decimals():
    assert segment("The value is 3.14 here. Next one.") == [
        "The value is 3.14 here.",
        "Next one.",
    ]


def test_splits_after_a_number_that_ends_a_sentence():
    """Regression: a blanket "never split after a digit" rule silently welded
    together every sentence ending in a number."""
    assert segment("If the goal is met, write 0. If it is impossible, write -1.") == [
        "If the goal is met, write 0.",
        "If it is impossible, write -1.",
    ]


def test_splits_after_a_year():
    assert segment("It was published in 2020. The second edition followed.") == [
        "It was published in 2020.",
        "The second edition followed.",
    ]


def test_keeps_a_numbered_list_marker_with_its_item():
    assert segment("1. Choose any one of the 16 pieces.") == [
        "1. Choose any one of the 16 pieces."
    ]


def test_does_not_split_on_initials():
    assert segment("Written by J. Smith. Published in 2020.") == [
        "Written by J. Smith.",
        "Published in 2020.",
    ]


def test_does_not_split_figure_references():
    assert segment("See Fig. 2 for details. It shows the diagram.") == [
        "See Fig. 2 for details.",
        "It shows the diagram.",
    ]


def test_splits_on_question_and_exclamation():
    assert segment("What is a key? It identifies an entity!") == [
        "What is a key?",
        "It identifies an entity!",
    ]


def test_lowercase_after_period_is_not_a_boundary():
    # Typical of a mid-sentence abbreviation we do not know about; refusing to
    # split is the safe outcome.
    assert segment("Defined in sec. two of the notes.") == [
        "Defined in sec. two of the notes."
    ]


def test_unpunctuated_text_is_one_unit():
    assert segment("Relationship Sets") == ["Relationship Sets"]


def test_empty_text_yields_nothing():
    assert segment("   ") == []


def test_collapses_whitespace():
    assert segment("a  b\n c") == ["a b c"]

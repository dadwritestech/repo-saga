"""The fact checker is the product. These pin its rules."""
from saga.write import check_line

FACTS = {"F1": {"text": 'On 2014-02-19 TJ Holowaychuk committed "update node-fresh" after 3,881 commits.'}}
AUTHORS = {"TJ Holowaychuk", "Max Riveiro"}


def errs(text, cites=("F1",)):
    return check_line({"text": text, "cites": list(cites)}, FACTS, AUTHORS)


def test_clean_sentence_passes():
    assert errs('On February 19, 2014, TJ Holowaychuk committed "update node-fresh".') == []


def test_needs_cites():
    assert errs("Something happened.", cites=())


def test_number_must_be_in_cited_fact():
    assert errs("TJ Holowaychuk made 4,000 commits.")
    assert errs("TJ Holowaychuk made 3,881 commits.") == []


def test_unknown_author_rejected():
    assert errs("Max Riveiro committed it.")


def test_quote_must_be_verbatim():
    assert errs('TJ Holowaychuk committed "rewrite everything".')


def test_endings_rejected():
    assert errs("The founder era reached its conclusion.")
    assert errs("By the end of the decade it was over.")


def test_gendered_pronouns_rejected_but_not_inside_words():
    assert errs("He committed it.")
    assert errs("The history then shifted.") == []


def test_no_exclamations():
    assert errs("What a commit!")

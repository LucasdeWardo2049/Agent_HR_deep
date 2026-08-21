"""The Candidates column must carry a labelled link, without guessing the locale.

Verified against the real API: in a pt_BR spreadsheet a comma-separated HYPERLINK
renders as #ERROR!, while the semicolon form renders the label.
"""

from app.google_workspace import candidate_cv_cell, formula_separator

URL = "http://localhost:8000/api/v1/talent/candidates/candidate_abc/cv"


def test_comma_decimal_locales_use_a_semicolon() -> None:
    assert formula_separator("pt_BR") == ";"
    assert formula_separator("es_ES") == ";"
    assert formula_separator("de-DE") == ";"


def test_period_decimal_locales_use_a_comma() -> None:
    assert formula_separator("en_US") == ","
    assert formula_separator("ja_JP") == ","


def test_unknown_locale_is_not_guessed() -> None:
    assert formula_separator("xx_YY") is None
    assert formula_separator("") is None
    assert formula_separator(None) is None


def test_cell_is_a_labelled_formula_when_the_locale_is_known() -> None:
    assert candidate_cv_cell(URL, ";") == f'=HYPERLINK("{URL}";"Currículo")'
    assert candidate_cv_cell(URL, ",") == f'=HYPERLINK("{URL}","Currículo")'


def test_cell_degrades_to_a_plain_url_instead_of_a_broken_formula() -> None:
    """An unknown locale must never produce #ERROR! in the client's report."""
    assert candidate_cv_cell(URL, None) == URL


def test_empty_url_stays_empty() -> None:
    assert candidate_cv_cell("", ";") == ""

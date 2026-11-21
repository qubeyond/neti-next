"""Проверяет сопоставление URL с эталонными адресами в eval."""

from evals.compare_search import url_key


def test_percent_escape_case_does_not_change_the_retrieval_identity():
    assert url_key("https://example.org/%D0%90/") == url_key("https://example.org/%d0%90")


def test_encoded_separator_is_distinct_from_a_path_separator():
    assert url_key("https://example.org/record%2Fappendix") != url_key(
        "https://example.org/record/appendix"
    )

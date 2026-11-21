from datetime import date

from evals.experiment_fact_store import FactStore


def test_fact_store_filters_expired_snapshots_and_preserves_historical_campaign():
    store = FactStore()
    url = "https://university.example/admission"
    question = "Когда начинается приём документов?"
    old = {"label": question, "title": "Приём", "quote": "20 июня", "year": 2025}
    current = {**old, "quote": "25 июня", "year": 2026}
    assert store.publish(url, "v1", date(2025, 6, 1), date(2025, 7, 1), [old])
    assert store.search(question, today=date(2026, 6, 1)) == []
    assert store.publish(url, "v2", date(2026, 6, 1), date(2026, 7, 1), [current])
    assert store.publish(url, "v2", date(2026, 6, 2), date(2026, 7, 2), [current])
    assert not store.publish(url, "v3", date(2026, 6, 3), date(2026, 7, 3), [])
    assert not store.publish(url, "v3", date(2026, 6, 3), date(2026, 7, 3), [old], complete=False)
    assert not store.publish(url, "v1", date(2025, 6, 1), date(2025, 7, 1), [old])

    assert [r["snippet"] for r in store.search(question, today=date(2026, 6, 4))] == [
        "25 июня",
    ]
    assert [r["snippet"] for r in store.search(question, today=date(2026, 6, 4), year=2025)] == [
        "20 июня"
    ]

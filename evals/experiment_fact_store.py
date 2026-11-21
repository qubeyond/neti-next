"""Проверяет локальные карточки с дословными доказательствами и версиями."""

import json
import sqlite3
from datetime import date

from evals.experiment_rerank import fts_query


class FactStore:
    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(
            """
            CREATE TABLE snapshots (
                url TEXT, version TEXT, checked_on TEXT, expires_on TEXT,
                PRIMARY KEY (url, version)
            );
            CREATE TABLE active (url TEXT PRIMARY KEY, version TEXT);
            CREATE VIRTUAL TABLE facts USING fts5(
                label, quote, url UNINDEXED, version UNINDEXED,
                title UNINDEXED, year UNINDEXED, owners UNINDEXED
            );
            """
        )

    def publish(
        self,
        url: str,
        version: str,
        checked_on: date,
        expires_on: date,
        facts: list[dict],
        *,
        complete: bool = True,
    ) -> bool:
        if not complete or not facts or expires_on < checked_on:
            return False
        with self.db:
            current = self.db.execute(
                "SELECT checked_on FROM snapshots JOIN active USING (url, version) WHERE url = ?",
                (url,),
            ).fetchone()
            if current and checked_on.isoformat() < current[0]:
                return False
            existing = self.db.execute(
                "SELECT 1 FROM snapshots WHERE url = ? AND version = ?",
                (url, version),
            ).fetchone()
            if existing:
                self.db.execute(
                    "UPDATE snapshots SET checked_on = ?, expires_on = ? WHERE url = ? AND "
                    "version = ?",
                    (checked_on.isoformat(), expires_on.isoformat(), url, version),
                )
            else:
                self.db.execute(
                    "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                    (url, version, checked_on.isoformat(), expires_on.isoformat()),
                )
                self.db.executemany(
                    "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        (
                            card["label"],
                            card["quote"],
                            url,
                            version,
                            card["title"],
                            card["year"],
                            json.dumps(card.get("owners", [])),
                        )
                        for card in facts
                    ],
                )
            self.db.execute("INSERT OR REPLACE INTO active VALUES (?, ?)", (url, version))
        return True

    def search(
        self,
        question: str,
        *,
        today: date,
        year: int | None = None,
        excluded_owner: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        query = fts_query(question)
        if not query:
            return []
        rows = self.db.execute(
            """
            SELECT facts.url, title, quote, owners
            FROM facts JOIN snapshots
               ON snapshots.url = facts.url AND snapshots.version = facts.version
            LEFT JOIN active
               ON active.url = facts.url AND active.version = facts.version
            WHERE facts MATCH ? AND (
               (? IS NULL AND active.version IS NOT NULL AND expires_on >= ?)
               OR (? IS NOT NULL AND year = ?)
            )
            ORDER BY bm25(facts, 2.0, 1.0), snapshots.checked_on DESC, facts.rowid
            """,
            (query, year, today.isoformat(), year, year),
        )
        results = []
        for url, title, quote, owners in rows:
            if excluded_owner is not None and excluded_owner in json.loads(owners):
                continue
            results.append({"url": url, "title": title, "snippet": quote})
            if len(results) == limit:
                break
        return results

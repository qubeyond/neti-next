"""Хранит ограниченное число публичных документов в PostgreSQL."""

import hashlib
import logging
from collections.abc import Callable
from threading import Lock
from time import time_ns
from urllib.parse import urlsplit

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from agent.domain.recovery import document_key
from agent.runtime import REQUEST_ID, remaining_time

_EXTRACTOR = "firecrawl-markdown-v2"
_SCHEMA_LOCK = 4_615_021
_NANOSECONDS_PER_SECOND = 1_000_000_000
logger = logging.getLogger("uvicorn.error.agent")
_LOCKS = tuple(Lock() for _ in range(16))
_CACHE_LOCK = Lock()
_caches: dict[str, "DocumentCache"] = {}


class DocumentCache:
    def __init__(self, dsn: str) -> None:
        self.pool = ConnectionPool(
            dsn,
            kwargs={
                "connect_timeout": 2,
                "options": "-c statement_timeout=1200 -c lock_timeout=500",
            },
            min_size=0,
            max_size=4,
            timeout=1,
            max_waiting=8,
            open=True,
            name="agent-document-cache",
        )
        self.schema_lock = Lock()
        self.ready = False

    def _ensure_schema(self) -> None:
        with self.schema_lock:
            if self.ready:
                return

            with self.pool.connection(timeout=remaining_time(1)) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS document_cache.documents (
                        address TEXT PRIMARY KEY,
                        url TEXT NOT NULL,
                        extractor TEXT NOT NULL,
                        digest TEXT NOT NULL,
                        verified_at BIGINT NOT NULL,
                        markdown TEXT NOT NULL
                    )
                """)
                connection.execute("""
                    CREATE INDEX IF NOT EXISTS documents_expiration
                    ON document_cache.documents (verified_at DESC, address)
                """)

            self.ready = True

    def lookup(self, address: str, ttl: int) -> str:
        self._ensure_schema()

        with self.pool.connection(timeout=remaining_time(1)) as connection:
            row = connection.execute(
                "SELECT markdown FROM document_cache.documents WHERE address = %s "
                "AND extractor = %s AND verified_at >= %s",
                (address, _EXTRACTOR, time_ns() - ttl * _NANOSECONDS_PER_SECOND),
            ).fetchone()

            return row[0] if row else ""

    def store(self, address: str, url: str, markdown: str, ttl: int, capacity: int) -> None:
        now = time_ns()
        digest = hashlib.sha256(markdown.encode()).hexdigest()

        with self.pool.connection(timeout=remaining_time(1)) as connection:
            connection.execute("LOCK TABLE document_cache.documents IN SHARE ROW EXCLUSIVE MODE")
            connection.execute(
                """
                INSERT INTO document_cache.documents
                    (address, url, extractor, digest, verified_at, markdown)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (address) DO UPDATE SET
                    url = excluded.url, extractor = excluded.extractor, digest = excluded.digest,
                    verified_at = excluded.verified_at, markdown = excluded.markdown
                """,
                (address, url, _EXTRACTOR, digest, now, markdown),
            )
            connection.execute(
                "DELETE FROM document_cache.documents WHERE verified_at < %s",
                (now - ttl * _NANOSECONDS_PER_SECOND,),
            )
            connection.execute(
                """
                DELETE FROM document_cache.documents WHERE address IN (
                    SELECT address FROM document_cache.documents
                    ORDER BY verified_at DESC, address OFFSET %s
                )
                """,
                (capacity,),
            )


def _get_cache(dsn: str) -> DocumentCache:
    with _CACHE_LOCK:
        cache = _caches.get(dsn)

        if cache is None:
            cache = DocumentCache(dsn)
            _caches[dsn] = cache

        return cache


def close_document_cache() -> None:
    with _CACHE_LOCK:
        for cache in _caches.values():
            cache.pool.close()

        _caches.clear()


def cached_document(
    url: str, read: Callable[[str], str] | None, *, dsn: str, ttl: int, capacity: int
) -> str:
    key = document_key(url)

    if key is None:
        return ""

    address = repr((urlsplit(url).scheme, key))
    lock = _LOCKS[hash(key) % len(_LOCKS)]

    if not lock.acquire(timeout=remaining_time(2)):
        return ""

    try:
        return _read_or_refresh(address, url, read, dsn=dsn, ttl=ttl, capacity=capacity)
    finally:
        lock.release()


def _read_or_refresh(
    address: str,
    url: str,
    read: Callable[[str], str] | None,
    *,
    dsn: str,
    ttl: int,
    capacity: int,
) -> str:
    cache = None

    try:
        cache = _get_cache(dsn)
        cached = cache.lookup(address, ttl)

        if cached:
            logger.info("agent.document_cache request_id=%s hit=true", REQUEST_ID.get())
            return cached
    except (psycopg.Error, PoolTimeout) as error:
        cache = None
        logger.warning("agent.document_cache error=%s", type(error).__name__)

    if read is None:
        return ""

    logger.info("agent.document_cache request_id=%s hit=false", REQUEST_ID.get())

    document = read(url)

    if document and cache is not None:
        try:
            cache.store(address, url, document, ttl, capacity)
        except (psycopg.Error, PoolTimeout) as error:
            logger.warning("agent.document_cache error=%s", type(error).__name__)

    return document

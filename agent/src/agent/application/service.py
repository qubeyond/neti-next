"""Выполняет веб-ответ и при необходимости восстанавливает доказательства."""

from dataclasses import dataclass
from time import perf_counter

from agent.application.audit import Verification, verify_answer
from agent.domain.errors import AgentError
from agent.domain.grounding import check_answer, is_truncated
from agent.domain.ports import GenerateAnswer, ReadDocument, RewriteQuery, Search, SearchFactory
from agent.domain.query import information_question, public_query
from agent.domain.recovery import document_excerpt, document_key, read_candidate
from agent.domain.sources import SearchResult
from agent.runtime import phase, remaining_time


def _source_domains(results: list[SearchResult]) -> list[str]:
    domains = {}

    for result in results:
        if key := document_key(result["url"]):
            domains.setdefault(key[0], None)

    return list(domains)[:8]


def _grounded(response: dict) -> bool:
    citations = response["citations"]

    return (
        bool(citations)
        and all(item["verified"] for item in citations)
        and not response["malformed_citations"]
    )


@dataclass(frozen=True)
class AnswerService:
    search: Search
    generate: GenerateAnswer
    source_limit: int
    search_limit: int
    recovery_limit: int
    search_provider: str = ""
    backup_provider: str = ""
    verify: Verification | None = None
    repair: GenerateAnswer | None = None
    regenerate: GenerateAnswer | None = None
    cached: ReadDocument | None = None
    read: ReadDocument | None = None
    backup_search: SearchFactory | None = None
    rewrite: RewriteQuery | None = None

    def answer(self, question: str, *, model: str | None = None) -> dict:
        question = information_question(question)

        if not question:
            return {
                "status": "clarification",
                "answer": "Уточните, какую информацию о вузе вы ищете.",
                "citations": [],
            }

        search_query = public_query(question)

        if not search_query:
            return {
                "status": "clarification",
                "answer": "Уточните, какую информацию о вузе вы ищете.",
                "citations": [],
            }

        results, used_backup = self._search(search_query)

        reused = False

        if self.cached is not None and (index := read_candidate(question, results)) is not None:
            with phase("cache_lookup", service="storage") as trace:
                text = self.cached(results[index]["url"])
                trace["cache_hit"] = bool(text)
                trace["domains"] = _source_domains([results[index]])

                if text and (excerpt := document_excerpt(text, question, self.source_limit)):
                    results[index] = {**results[index], "snippet": excerpt}
                    reused = True

        with phase("generation"):
            initial = self.generate(question, results, model=model)

        with phase("grounding") as trace:
            initial = check_answer(question, initial)
            trace["decision"] = initial["status"]
            trace["count"] = len(initial["citations"])

        if self.repair is not None and (initial["status"] == "invalid" or is_truncated(initial)):
            try:
                remaining_time(1)

                with phase("repair"):
                    repaired = check_answer(question, self.repair(question, results, model=model))

                if repaired["status"] != "invalid" and not is_truncated(repaired):
                    initial = repaired
            except AgentError:
                pass

        if initial["status"] == "invalid" or is_truncated(initial):
            return {
                **initial,
                "status": "unverified",
                "answer": "Не удалось завершить проверенный ответ. Попробуйте уточнить вопрос.",
                "citations": [],
            }

        result = self.recover(
            question, results, initial, model=model, skip_read=reused, skip_search=used_backup
        )

        return verify_answer(self.verify, question, check_answer(question, result))

    def _search(self, query: str) -> tuple[list[SearchResult], bool]:
        try:
            with phase("search", service="web") as trace:
                trace["provider"] = self.search_provider
                results = self.search(query)
                trace["count"] = len(results)
                trace["domains"] = _source_domains(results)

            return results, False
        except AgentError as primary:
            if primary.status not in {429, 502, 503, 504} or self.backup_search is None:
                raise

            remaining_time(1)

            try:
                return self._search_backup(query), True
            except AgentError as fallback:
                raise primary from fallback

    def _search_backup(self, query: str) -> list[SearchResult]:
        with phase("backup_search", service="web") as trace:
            trace["provider"] = self.backup_provider

            if self.backup_search is None:
                return []

            results = self.backup_search()(query)
            trace["count"] = len(results)
            trace["domains"] = _source_domains(results)

        return results

    def recover(
        self,
        question: str,
        results: list[SearchResult],
        initial: dict,
        *,
        model: str | None = None,
        skip_read: bool = False,
        skip_search: bool = False,
    ) -> dict:
        if initial["status"] not in {"partial", "unverified"}:
            return initial

        if self.read is None and self.backup_search is None:
            return initial

        index = (
            read_candidate(question, results) if self.read is not None and not skip_read else None
        )
        action = "read" if index is not None else "search"

        if (index is not None and self.read is None) or (
            index is None and (skip_search or self.backup_search is None or self.rewrite is None)
        ):
            return initial

        started = perf_counter()
        outcome = "no_new_evidence"
        retry = None
        rewrite = None

        try:
            remaining_time(1)

            updated = list(results)

            if index is not None and self.read is not None:
                with phase("read", service="web") as trace:
                    text = self.read(results[index]["url"])
                    trace["count"] = int(bool(text))
                    trace["domains"] = _source_domains([results[index]])

                if text and (excerpt := document_excerpt(text, question, self.source_limit)):
                    updated[index] = {**updated[index], "snippet": excerpt}
            elif not skip_search and self.rewrite is not None:
                with phase("rewrite"):
                    query, rewrite = self.rewrite(question, results)

                seen = {document_key(result["url"]) for result in updated}
                additions = []

                found = self._search_backup(public_query(query) or public_query(question))

                for row in found:
                    if (key := document_key(row["url"])) and key not in seen:
                        seen.add(key)
                        additions.append(row)

                if additions:
                    additions = additions[: min(self.recovery_limit, self.search_limit)]
                    updated = [
                        *updated[: self.search_limit - len(additions)],
                        *additions,
                    ]

            if updated != results:
                with phase("regeneration"):
                    generate = self.regenerate or self.generate
                    retry = generate(question, updated, model=model)

                retry = check_answer(question, retry) if "sources" in retry else retry
                outcome = "kept_initial"

                improves = retry["status"] == "answered" or (
                    retry["status"] == "partial" and initial["status"] == "unverified"
                )

                if improves and _grounded(retry) and not is_truncated(retry):
                    outcome = "accepted_retry"
        except AgentError:
            outcome = "provider_error"

        response = retry if outcome == "accepted_retry" and retry is not None else initial

        return {
            **response,
            "recovery": {
                "action": action,
                "outcome": outcome,
                "latency_ms": round((perf_counter() - started) * 1000),
                "initial_usage": (initial.get("generation") or {}).get("usage"),
                "rewrite_usage": (rewrite or {}).get("usage"),
                "retry_usage": ((retry or {}).get("generation") or {}).get("usage"),
            },
        }

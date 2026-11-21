"""Выполняет веб-ответ и при необходимости восстанавливает доказательства."""

from dataclasses import dataclass
from time import perf_counter

from agent.shared.errors import AgentError

from ..domain.recovery import document_excerpt, document_key, read_candidate
from ..domain.sources import SearchResult
from .ports import GenerateAnswer, ReadDocument, RewriteQuery, Search, SearchFactory


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
    read: ReadDocument | None = None
    backup_search: SearchFactory | None = None
    rewrite: RewriteQuery | None = None
    source_limit: int = 1500
    search_limit: int = 5

    def answer(self, question: str, *, model: str | None = None) -> dict:
        question = question.strip()

        if not question:
            raise AgentError("Вопрос не должен быть пустым.")

        results = self.search(question)
        initial = self.generate(question, results, model=model)

        return self.recover(question, results, initial, model=model)

    def recover(
        self,
        question: str,
        results: list[SearchResult],
        initial: dict,
        *,
        model: str | None = None,
    ) -> dict:
        if initial["status"] not in {"partial", "unverified"}:
            return initial

        if self.read is None and self.backup_search is None:
            return initial

        index = read_candidate(question, results)
        action = "read" if index is not None else "search"

        if (index is not None and self.read is None) or (
            index is None and (self.backup_search is None or self.rewrite is None)
        ):
            return initial

        started = perf_counter()
        outcome = "no_new_evidence"
        retry = None
        rewrite = None

        try:
            updated = list(results)

            if index is not None and self.read is not None:
                text = self.read(results[index]["url"])

                if text and (excerpt := document_excerpt(text, question, self.source_limit)):
                    updated[index] = {**updated[index], "snippet": excerpt}
            elif self.backup_search is not None and self.rewrite is not None:
                search = self.backup_search()
                query, rewrite = self.rewrite(question, results)
                seen = {document_key(result["url"]) for result in updated}
                additions = []

                for row in search(query):
                    if (key := document_key(row["url"])) and key not in seen:
                        seen.add(key)
                        additions.append(row)

                if additions:
                    additions = additions[: min(2, self.search_limit)]
                    updated = [
                        *updated[: self.search_limit - len(additions)],
                        *additions,
                    ]

            if updated != results:
                retry = self.generate(question, updated, model=model)
                outcome = "kept_initial"

                if retry["status"] == "answered" and _grounded(retry):
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

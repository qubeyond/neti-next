"""Отбирает фрагменты сниппета в пределах бюджета источника."""

import re

WORD = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)
CLAUSE_END = re.compile(r";|[.!?](?=\s+[А-ЯЁA-Z]|\s*$)")
QUERY_STOP_WORDS = {
    "можно",
    "нужно",
    "найти",
    "какого",
    "какие",
    "согласно",
    "ответь",
    "сколько",
    "указано",
    "какой",
    "какая",
    "какое",
    "каким",
    "расположено",
}


def prefix_with_evidence(
    text: str, question: str, limit: int, *, rank_clauses: bool = False
) -> str:
    prefix = text[:limit]
    query = {
        word[:5]
        for word in WORD.findall(question.lower())
        if len(word) >= 4 and word not in QUERY_STOP_WORDS
    }
    missing = query - {word[:5] for word in WORD.findall(prefix.lower())}

    if not missing or len(text) <= limit:
        return prefix

    separator = " ... "
    candidates = []
    start = 0

    for match in CLAUSE_END.finditer(text):
        end = match.end()

        if end > limit:
            clause = text[start:end].strip()
            clause_words = {word[:5] for word in WORD.findall(clause.lower())}
            terms = missing & clause_words

            if terms and len(clause) <= min(220, limit // 3):
                score = (len(terms), len(query & clause_words) if rank_clauses else 0)
                candidates.append((score, clause))

        start = end

    if not candidates:
        return prefix

    _, evidence = max(candidates, key=lambda item: item[0])
    head = text[: limit - len(separator) - len(evidence)].rstrip()

    return f"{head}{separator}{evidence}"

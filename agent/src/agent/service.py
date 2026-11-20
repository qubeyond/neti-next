from .config import AgentError
from .search import make_searcher
from .web_baseline import answer_from_search


def answer_question(question: str, *, model: str | None = None) -> dict:
    question = question.strip()
    if not question:
        raise AgentError("Вопрос не должен быть пустым.")
    return answer_from_search(question, make_searcher()(question), model=model)

"""Предоставляет общий пул HTTP-соединений и ограниченное чтение внешних ответов."""

import json

from httpx2 import Client, Request, TimeoutException

from agent.domain.errors import AgentError
from agent.runtime import remaining_time

MAX_SEARCH_RESPONSE_BYTES = 4 * 1024 * 1024
client = Client()


def send_json(request: Request, timeout: int, *, transport: Client = client):
    try:
        with transport.stream(
            request.method,
            request.url,
            headers=request.headers,
            content=request.content,
            timeout=remaining_time(timeout),
            follow_redirects=False,
        ) as response:
            response.raise_for_status()

            body = bytearray()

            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                remaining_time(timeout)

                if len(body) + len(chunk) > MAX_SEARCH_RESPONSE_BYTES:
                    raise AgentError("Ответ поисковика превышает лимит размера.")

                body.extend(chunk)

            return json.loads(body)
    except TimeoutException:
        raise AgentError("Время ожидания поиска истекло.", status=504) from None

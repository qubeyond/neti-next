"""Проверяет совместимость форматов RouterAI с официальным OpenAI SDK."""

import json
from unittest import TestCase
from unittest.mock import patch

from httpx2 import MockTransport, Request, Response
from openai import DefaultHttpxClient, OpenAI

from inference.config import Settings
from inference.domain.entities import Generation, Message
from inference.domain.errors import InferenceError
from inference.providers.routerai import RouterAIProvider


def reply(body: dict, status: int = 200) -> Response:
    return Response(status, json=body)


class TestRouterAI(TestCase):
    def test_sdk_preserves_media_parameters_annotations_and_billing(self) -> None:
        env = {
            "ROUTERAI_API_KEY": "test-key",
            "ROUTERAI_BASE_URL": "http://127.0.0.1:9000/v1",
            "ROUTERAI_TIMEOUT_SECONDS": "9",
            "ASR_LANGUAGE": "en",
            "ASR_MODEL": "test-asr",
            "OCR_PROMPT": "Read the image",
            "OCR_MAX_TOKENS": "123",
            "PDF_OCR_PROMPT": "Process the page",
            "PDF_OCR_MAX_TOKENS": "12",
            "PDF_OCR_ENGINE": "test-engine",
        }

        with patch.dict("os.environ", env, clear=True):
            settings = Settings()

        chat = {
            "choices": [
                {
                    "message": {
                        "content": "Decoded",
                        "annotations": [
                            {"file": {"content": [{"type": "text", "text": "Decoded page"}]}}
                        ],
                    }
                }
            ],
            "usage": {"cost": 0.45},
        }
        requests: list[Request] = []
        replies = iter(
            [{"text": "Transcribed", "usage": {"seconds": 1.5, "cost": 0.12}}, chat, chat, chat]
        )

        def handler(request: Request) -> Response:
            requests.append(request)

            return reply(next(replies))

        with patch(
            "inference.providers.routerai.DefaultHttpxClient",
            return_value=DefaultHttpxClient(transport=MockTransport(handler)),
        ):
            provider = RouterAIProvider(settings.routerai)

            self.addCleanup(provider.close)

            voice = provider.transcribe(b"audio", "wav")

            provider.recognize_image(b"image", "image/png")

            scanned = provider.recognize_scan(b"page")

            provider.generate(
                Generation(
                    [Message("user", "Текст")],
                    max_tokens=8,
                    reasoning={"enabled": False},
                    response_format={"type": "json_object"},
                )
            )

        speech, image, pdf, generation = requests
        ocr, scan, llm = (json.loads(item.content) for item in (image, pdf, generation))

        self.assertEqual(speech.url.path, "/v1/audio/transcriptions")
        self.assertIn("multipart/form-data", speech.headers["content-type"])
        self.assertIn(b'name="language"\r\n\r\nen', speech.content)
        self.assertIn(b'name="model"\r\n\r\ntest-asr', speech.content)
        self.assertEqual(voice.usage, {"seconds": 1.5, "cost": 0.12})
        self.assertEqual(ocr["messages"][0]["content"][0]["text"], "Read the image")
        self.assertEqual(ocr["max_tokens"], 123)
        self.assertEqual(scan["messages"][0]["content"][0]["text"], "Process the page")
        self.assertEqual(scan["max_tokens"], 12)
        self.assertEqual(scan["plugins"][0]["pdf"]["engine"], "test-engine")
        self.assertEqual(scanned.model, "test-engine")
        self.assertEqual(scanned.fragments, ["Decoded page"])
        self.assertEqual(scanned.usage["cost"], 0.45)
        self.assertEqual(llm["reasoning"], {"enabled": False})
        self.assertEqual(llm["response_format"], {"type": "json_object"})
        self.assertEqual(speech.extensions["timeout"]["read"], 9)
        self.assertNotIn("test-key", repr(settings))

    def test_rejects_response_over_configured_limit(self) -> None:
        with patch.dict("os.environ", {"ROUTERAI_MAX_RESPONSE_BYTES": "3"}, clear=True):
            config = Settings().routerai

        with OpenAI(
            api_key="test-key",
            max_retries=0,
            http_client=DefaultHttpxClient(
                transport=MockTransport(lambda _: Response(200, content=b"1234"))
            ),
        ) as client:
            provider = RouterAIProvider(config, client)

            with self.assertRaises(InferenceError) as error:
                provider.transcribe(b"audio", "wav")

        self.assertEqual(error.exception.status_code, 502)

    def test_provider_429_does_not_trigger_sdk_retries(self) -> None:
        with patch.dict("os.environ", {"ROUTERAI_API_KEY": "test-key"}, clear=True):
            config = Settings().routerai

        hits = []

        def handler(request: Request) -> Response:
            hits.append(request)

            return reply({"error": {"message": "busy"}}, status=429)

        with patch(
            "inference.providers.routerai.DefaultHttpxClient",
            return_value=DefaultHttpxClient(transport=MockTransport(handler)),
        ):
            provider = RouterAIProvider(config)

            self.addCleanup(provider.close)

            with self.assertRaises(InferenceError) as error:
                provider.transcribe(b"audio", "wav")

        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(len(hits), 1)

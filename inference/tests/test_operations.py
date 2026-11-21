"""Проверяет валидацию, маршрутизацию и метрики операций."""

from unittest import TestCase
from unittest.mock import Mock

from inference.application.operations import Operations
from inference.config import Limits
from inference.domain.entities import Completion, Generation, Message, Recognition, Scan
from inference.domain.errors import InferenceError
from inference.domain.ports import Provider
from inference.runtime.execution import Execution
from inference.runtime.telemetry import REQUEST_ID


class TestOperations(TestCase):
    def setUp(self) -> None:
        self.provider = Mock(spec=Provider)
        self.limits = Limits(llm_concurrency=1, max_file_bytes=8, max_output_tokens=8)
        self.execution = Execution(self.limits)
        self.operations = Operations(self.provider, self.execution, self.limits)

    def test_voice_dispatches_decoded_audio_and_preserves_usage(self) -> None:
        self.provider.transcribe.return_value = Recognition(
            "Расписание НГУ", "whisper", {"seconds": 3.5}
        )

        result = self.operations.transcribe("YXVkaW8=", "audio/mp4")

        self.provider.transcribe.assert_called_once_with(b"audio", "m4a")
        self.assertEqual(result.value.text, "Расписание НГУ")
        self.assertIn(
            'neti_inference_seconds_total{operation="asr"} 3.5', self.execution.metrics().decode()
        )

    def test_prepared_pdf_goes_directly_to_ocr(self) -> None:
        self.provider.recognize_scan.return_value = Scan(["А", "Б"], "ocr", {"cost": 0.45})

        result = self.operations.recognize("cGFnZQ==", "application/pdf")

        self.provider.recognize_scan.assert_called_once_with(b"page")
        self.assertEqual(result.value, Recognition("А\n\nБ", "ocr", {"cost": 0.45}))

    def test_limits_reject_large_files_and_generation_before_provider(self) -> None:
        with self.assertRaises(InferenceError) as file_error:
            self.operations.recognize("MTIzNDU2Nzg5", "image/png")

        with self.assertRaises(InferenceError) as generation_error:
            self.operations.generate(Generation([Message("user", "Текст")], max_tokens=9))

        self.assertEqual(file_error.exception.status_code, 413)
        self.assertEqual(generation_error.exception.status_code, 422)
        self.provider.recognize_image.assert_not_called()
        self.provider.generate.assert_not_called()

    def test_provider_failure_releases_capacity_and_logs_without_input(self) -> None:
        self.provider.generate.side_effect = [
            InferenceError(502, "private-error"),
            Completion("private-output", "llm", {"total_tokens": 4}),
        ]
        request = Generation([Message("user", "private-input")], max_tokens=4)
        marker = REQUEST_ID.set("test-run")

        try:
            with self.assertLogs("neti.inference") as logs:
                with self.assertRaises(InferenceError):
                    self.operations.generate(request)

                result = self.operations.generate(request)
        finally:
            REQUEST_ID.reset(marker)

        records = "\n".join(logs.output)

        self.assertEqual(result.value.answer, "private-output")
        self.assertIn(
            'neti_inference_failed_total{operation="llm"} 1.0', self.execution.metrics().decode()
        )
        self.assertIn('"request_id": "test-run"', records)
        self.assertIn('"total_tokens": 4', records)
        self.assertNotIn("private-", records)

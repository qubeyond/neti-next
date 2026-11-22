"""Проверяет безопасный формат ошибок и авторизацию HTTP."""

from unittest import TestCase

from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from inference.app import create_app
from inference.config import Settings


class TestHTTP(TestCase):
    def test_validation_error_excludes_submitted_data(self) -> None:
        with TestClient(create_app(Settings(token=SecretStr("")))) as client:
            response = client.post(
                "/inference/v1/ocr",
                json={"mime": "image/png", "data": {"private-probe": "sensitive"}},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("x-request-id", response.headers)
        self.assertNotIn("private-probe", response.text)
        self.assertNotIn("sensitive", response.text)
        self.assertEqual(set(response.json()["errors"][0]), {"loc", "type"})

    def test_protected_route_accepts_standard_bearer_authentication(self) -> None:
        with TestClient(create_app(Settings(token=SecretStr("test-key")))) as client:
            denied = client.get("/inference/v1/capabilities")
            accepted = client.get(
                "/inference/v1/capabilities", headers={"Authorization": "bearer test-key"}
            )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)

    def test_configuration_errors_hide_bad_values(self) -> None:
        with self.assertRaises(ValidationError) as error:
            Settings.model_validate(
                {"routerai": {"base_url": "https://private-url-value@routerai.ru/"}}
            )

        self.assertNotIn("private-url-value", str(error.exception))

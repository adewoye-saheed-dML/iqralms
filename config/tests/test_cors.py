from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

User = get_user_model()

class CorsIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.token = Token.objects.create(user=self.user)

    def test_options_preflight_from_localhost_succeeds(self):
        # 1. OPTIONS preflight from http://localhost:3000 succeeds.
        response = self.client.options(
            "/api/accounts/me/",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
        )
        self.assertEqual(response.status_code, 200)

        # 2. OPTIONS preflight includes the appropriate CORS headers.
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertIn("authorization", response.get("Access-Control-Allow-Headers", "").lower())

    def test_disallowed_origin_is_not_permitted(self):
        # 5. A disallowed origin is not automatically permitted.
        response = self.client.options(
            "/api/accounts/me/",
            HTTP_ORIGIN="http://malicious.example.com",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization",
        )
        # Without an allowed origin, django-cors-headers doesn't add the allow-origin header
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_unauthenticated_api_request_returns_401(self):
        # 3. An actual unauthenticated API request still returns 401.
        response = self.client.get(
            "/api/accounts/me/",
            HTTP_ORIGIN="http://localhost:3000",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")

    def test_token_authenticated_api_request_remains_protected(self):
        # 4. Token-authenticated API requests remain protected.
        response = self.client.get(
            "/api/accounts/me/",
            HTTP_ORIGIN="http://localhost:3000",
            HTTP_AUTHORIZATION=f"Token {self.token.key}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "http://localhost:3000")

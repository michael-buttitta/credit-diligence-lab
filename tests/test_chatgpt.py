import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
import jwt

import analyze
import chatgpt_auth as auth


class ChatGPTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def test_identity_checks(self):
        claims = {
            "iss": auth.ISSUER, "aud": "test-client", "sub": "test-subject",
            "iat": int(time.time()), "exp": int(time.time()) + 300, "nonce": "test-nonce",
        }
        with patch.object(auth.jwt, "PyJWKClient") as jwks:
            jwks.return_value.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.key.public_key())
            token = jwt.encode(claims, self.key, algorithm="RS256")
            self.assertEqual(auth.verify_identity(token, "test-client", nonce="test-nonce")["sub"], "test-subject")
            for arguments in (
                {"client_id": "wrong-audience"},
                {"client_id": "test-client", "nonce": "wrong-nonce"},
                {"client_id": "test-client", "subject": "wrong-subject"},
            ):
                with self.subTest(arguments=arguments), self.assertRaises(RuntimeError):
                    auth.verify_identity(token, **arguments)
            wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            forged = jwt.encode(claims, wrong_key, algorithm="RS256")
            with self.assertRaises(RuntimeError):
                auth.verify_identity(forged, "test-client")
            expired = jwt.encode({**claims, "exp": int(time.time()) - 30}, self.key, algorithm="RS256")
            with self.assertRaises(RuntimeError):
                auth.verify_identity(expired, "test-client")

    def test_protected_storage_and_pending_refresh_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            connection = auth.ChatGPTConnection(Path(temporary))
            state = connection.read()
            tokens = {
                "access_token": "new-access", "refresh_token": "new-refresh",
                "id_token": "test-id-token", "expires_in": 3600, "token_type": "Bearer",
                "scope": "resource.invoke chatgpt.tokens.use.direct",
            }
            account = {
                "id": "account-1", "client_id": "test-client", "subject": "test-subject",
                "scopes": tokens["scope"].split(),
                "tokens": {**tokens, "access_token": "old-access"},
                "expires_at": time.time() - 1,
                "pending_refresh": {"tokens": tokens, "received_at": time.time()},
            }
            state["accounts"] = [account]
            state["active"] = account["id"]
            connection.save(state)
            self.assertEqual(connection.read(), state)
            if auth.os.name == "nt":
                self.assertNotIn(b"new-refresh", connection.filename.read_bytes())
            with patch.object(auth, "json_request") as request, patch.object(
                auth, "verify_identity", return_value={"sub": "test-subject"},
            ):
                self.assertEqual(connection.access_token(), "new-access")
                request.assert_not_called()
            self.assertNotIn("pending_refresh", connection.read()["accounts"][0])
            with self.assertRaises(RuntimeError):
                connection.require_permission({"scopes": ["openid"]})

    def test_stream_requires_terminal_event(self):
        completed = {"status": "completed", "output": []}
        events = (
            b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
            + ("data: " + json.dumps({"type": "response.completed", "response": completed}) + "\n\n").encode()
        )
        self.assertEqual(analyze.read_stream(io.BytesIO(events)), completed)
        with self.assertRaises(RuntimeError):
            analyze.read_stream(io.BytesIO(b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'))
        with self.assertRaisesRegex(RuntimeError, "subscription_sharing_usage_limit_exceeded"):
            analyze.read_stream(io.BytesIO(
                b'data: {"type":"response.failed","response":{"error":{"code":"subscription_sharing_usage_limit_exceeded"}}}\n\n'
            ))

    def test_request_contract_and_billing(self):
        response = {"model": "test-model", "status": "completed", "output": []}
        stream = ("data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n").encode()
        with patch.object(analyze, "urlopen", return_value=io.BytesIO(stream)) as request:
            self.assertEqual(
                analyze.analyze("question", ["line"], "test-token", chatgpt=True, model="test-model"),
                response,
            )
            payload = json.loads(request.call_args.args[0].data)
            self.assertIsInstance(payload["input"], list)
            self.assertTrue(payload["stream"])
            self.assertFalse(payload["store"])
            self.assertNotIn("max_output_tokens", payload)
        with patch.object(analyze, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as request:
            analyze.analyze("question", ["line"], "test-key")
            payload = json.loads(request.call_args.args[0].data)
            self.assertEqual(payload["max_output_tokens"], 900)
            self.assertIsInstance(payload["input"], str)
        usage = {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 500}, "output_tokens": 200}
        self.assertEqual(analyze.cost_report(usage)["estimated_cost_usd"], 0.0013125)
        self.assertIsNone(analyze.cost_report(usage, chatgpt=True)["estimated_cost_usd"])


if __name__ == "__main__":
    unittest.main()

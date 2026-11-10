"""Opt-in smoke tests for the isolated gematik reference server.

These inspect untrusted metadata for interoperability only, not for authentication.
"""

import base64
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = os.environ.get("GSI_TEST_BASE_URL")


def request(path, data=None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}
    with urlopen(Request(f"{BASE_URL}{path}", data=data, headers=headers), timeout=10) as response:
        return response.status, response.read()


@unittest.skipUnless(BASE_URL, "Set GSI_TEST_BASE_URL to run the reference server smoke tests")
class ReferenceServerTests(unittest.TestCase):
    def test_entity_statement_exposes_expected_provider_metadata(self):
        status, body = request("/.well-known/openid-federation")
        self.assertEqual(status, 200)
        parts = body.decode("utf-8").split(".")
        self.assertEqual(len(parts), 3)
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        statement = json.loads(base64.urlsafe_b64decode(payload))
        provider = statement["metadata"]["openid_provider"]
        self.assertEqual(statement["iss"], BASE_URL)
        self.assertEqual(statement["sub"], BASE_URL)
        self.assertEqual(provider["issuer"], BASE_URL)
        self.assertEqual(provider["pushed_authorization_request_endpoint"], f"{BASE_URL}/PAR_Auth")
        self.assertTrue(provider["require_pushed_authorization_requests"])

    def test_management_health(self):
        management_url = os.environ.get("GSI_TEST_MANAGEMENT_URL")
        self.assertTrue(management_url, "GSI_TEST_MANAGEMENT_URL is required")
        with urlopen(f"{management_url}/actuator/health", timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response)["status"], "UP")

    def test_incomplete_par_is_rejected(self):
        with self.assertRaises(HTTPError) as error:
            request("/PAR_Auth", data=b"")
        try:
            self.assertGreaterEqual(error.exception.code, 400)
            self.assertLess(error.exception.code, 500)
        finally:
            error.exception.close()


if __name__ == "__main__":
    unittest.main()

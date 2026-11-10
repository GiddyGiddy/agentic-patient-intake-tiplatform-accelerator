"""Verify the pinned test Fedmaster -> IdP statement chain, not a production federation."""

import time
import unittest

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import ec

from test_rp_flow import ISSUER, SERVER

FEDMASTER = "http://127.0.0.1:8083"
FEDMASTER_NETWORK = "http://gsi-fedmaster:8083"
FEDMASTER_TEST_KEY = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAExJixkQv9D5ZR1JuqobDKcFfAcv0b
IJn8IQ4cLRwZuRqKYE65uvDtbP+bZDFmkwpsy/s5K8LBpHOy5tTFK39kDQ==
-----END PUBLIC KEY-----"""


def signed_statement(raw, key, issuer, subject):
    claims = jwt.decode(raw, key, algorithms=["ES256"], issuer=issuer,
                        options={"require": ["iss", "sub", "iat", "exp"]})
    if claims["sub"] != subject:
        raise ValueError("unexpected entity subject")
    for field in ("metadata_policy", "metadata_policy_crit", "constraints"):
        if field in claims:
            raise ValueError(f"unsupported federation {field}")
    return claims


class FederationChainTests(unittest.TestCase):
    def test_unsupported_policy_and_constraints_are_rejected(self):
        key = ec.generate_private_key(ec.SECP256R1())
        now = int(time.time())
        claims = {"iss": FEDMASTER, "sub": ISSUER, "iat": now, "exp": now + 60}
        for field, value in (
            ("metadata_policy", {"openid_provider": {}}),
            ("metadata_policy_crit", ["example"]),
            ("constraints", {"max_path_length": 0}),
        ):
            with self.subTest(field=field):
                token = jwt.encode({**claims, field: value}, key, algorithm="ES256")
                with self.assertRaisesRegex(ValueError, "unsupported federation"):
                    signed_statement(token, key.public_key(), FEDMASTER, ISSUER)

    def test_registered_idp_chain(self):
        response = requests.get(f"{FEDMASTER_NETWORK}/.well-known/openid-federation", timeout=10)
        response.raise_for_status()
        root = signed_statement(response.text, FEDMASTER_TEST_KEY, FEDMASTER, FEDMASTER)
        endpoint = root["metadata"]["federation_entity"]["federation_fetch_endpoint"]
        self.assertTrue(endpoint.startswith(f"{FEDMASTER}/"))
        network_endpoint = FEDMASTER_NETWORK + endpoint[len(FEDMASTER):]
        member = requests.get(network_endpoint, params={"iss": FEDMASTER, "sub": ISSUER}, timeout=10)
        member.raise_for_status()
        idp_statement = signed_statement(member.text, FEDMASTER_TEST_KEY, FEDMASTER, ISSUER)
        keys = [key for key in idp_statement["jwks"]["keys"] if key.get("kid") == "puk_idp_sig"]
        self.assertEqual(len(keys), 1)
        key = jwt.PyJWK.from_dict(keys[0], algorithm="ES256").key
        response = requests.get(f"{SERVER}/.well-known/openid-federation", timeout=10)
        response.raise_for_status()
        idp = signed_statement(response.text, key, ISSUER, ISSUER)
        self.assertIn(FEDMASTER, idp["authority_hints"])
        self.assertEqual(idp["metadata"]["openid_provider"]["issuer"], ISSUER)
        with self.assertRaises(jwt.InvalidSignatureError):
            signed_statement(response.text, FEDMASTER_TEST_KEY, ISSUER, ISSUER)
        with self.assertRaises(ValueError):
            signed_statement(member.text, FEDMASTER_TEST_KEY, FEDMASTER, "http://wrong-idp")

    def test_fedmaster_rejects_unregistered_idp(self):
        response = requests.get(f"{FEDMASTER_NETWORK}/.well-known/openid-federation", timeout=10)
        response.raise_for_status()
        root = signed_statement(response.text, FEDMASTER_TEST_KEY, FEDMASTER, FEDMASTER)
        endpoint = root["metadata"]["federation_entity"]["federation_fetch_endpoint"]
        self.assertTrue(endpoint.startswith(f"{FEDMASTER}/"))
        response = requests.get(FEDMASTER_NETWORK + endpoint[len(FEDMASTER):],
                                params={"iss": FEDMASTER, "sub": "http://unknown-idp:9999"}, timeout=10)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()

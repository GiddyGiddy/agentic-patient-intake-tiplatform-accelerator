"""Isolated test RP for the pinned local gematik server; not an application login."""

import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from jwcrypto import jwk, jwe


SERVER = "http://127.0.0.1:8085"
ISSUER = "http://127.0.0.1:8085"
FEDERATION_REMOTE = os.environ.get("GSI_FEDERATION_REMOTE") == "true"
# The remote value is published in the upstream Fedmaster's RP statement.
REDIRECT_URI = "http://127.0.0.1:8083/auth" if FEDERATION_REMOTE else "http://localhost:8085/AS"
SCOPE = "urn:telematik:versicherter openid"
KVNR = "X110000001"

# Public key from gsi-server/src/main/resources/keys/ref-gsi-sig-pubkey.pem
# at the commit pinned in Dockerfile; this is a test trust anchor, not a TI root.
GSI_TEST_KEY = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAESWsunGOo93GAcpdgrk0J+ARO9/KL
rBgQtVmLATUTu2jKVGWI8j6W9Ij0L+Zbo9fnRbV3aGcjB7gHrpFr9zHqLg==
-----END PUBLIC KEY-----"""


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def validate_id_token(token, key, client_id, nonce):
    claims = jwt.decode(token, key, algorithms=["ES256"], audience=client_id, issuer=ISSUER,
                        options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce"]})
    if not secrets.compare_digest(claims["nonce"], nonce):
        raise ValueError("ID token nonce mismatch")
    return claims


class ReferenceRpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encryption_key = jwk.JWK.generate(kty="EC", crv="P-256", use="enc", kid="test-enc")
        signing_key = (
            load_pem_private_key(Path("/tests/fixtures/ref-gsi-sig-privkey.pem").read_bytes(), None)
            if FEDERATION_REMOTE else ec.generate_private_key(ec.SECP256R1())
        )
        # The reference caches RP metadata by client ID for its entire lifetime.
        rp_path = "" if FEDERATION_REMOTE else f"/{secrets.token_hex(16)}"
        cls.client_id = f"http://127.0.0.1:8084{rp_path}"
        now = int(time.time())
        statement = {
            "iss": cls.client_id,
            "sub": cls.client_id,
            "iat": now,
            "exp": now + 600,
            "metadata": {
                "openid_relying_party": {
                    "redirect_uris": [REDIRECT_URI],
                    "scope": SCOPE,
                    "jwks": {"keys": [json.loads(cls.encryption_key.export_public())]},
                    "ti_features_supported": {"id_token_version_supported": ["1.0.0"]},
                }
            },
        }
        headers = {"typ": "entity-statement+jwt", "kid": "puk_fd_sig"}
        cls.signed = jwt.encode(statement, signing_key, algorithm="ES256", headers=headers).encode("ascii")
        cls.badly_signed = jwt.encode(
            statement, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256", headers=headers
        ).encode("ascii")
        cls.serve_invalid = False

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != f"{rp_path}/.well-known/openid-federation":
                    self.send_error(404)
                    return
                self.send_response(200)
                signed = cls.badly_signed if cls.serve_invalid else cls.signed
                self.send_header("Content-Type", "application/entity-statement+jwt")
                self.send_header("Content-Length", str(len(signed)))
                self.end_headers()
                self.wfile.write(signed)

            def log_message(self, format, *args):
                pass

        cls.rp_server = ThreadingHTTPServer(("127.0.0.1", 8084), Handler)
        cls.thread = threading.Thread(target=cls.rp_server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.rp_server.shutdown()
        cls.rp_server.server_close()
        cls.thread.join()

    def test_id_token_rejects_expired_or_wrong_issuer(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": self.client_id, "iat": now - 60,
            "exp": now - 1, "sub": "test-subject", "nonce": "test-nonce",
        }
        expired = jwt.encode(claims, private_key, algorithm="ES256")
        with self.assertRaises(jwt.ExpiredSignatureError):
            validate_id_token(expired, private_key.public_key(), self.client_id, "test-nonce")
        claims.update({"exp": now + 60, "iss": "http://wrong-issuer"})
        wrong_issuer = jwt.encode(claims, private_key, algorithm="ES256")
        with self.assertRaises(jwt.InvalidIssuerError):
            validate_id_token(wrong_issuer, private_key.public_key(), self.client_id, "test-nonce")

    def test_par_pkce_and_signed_encrypted_id_token(self):
        metadata_token = requests.get(f"{SERVER}/.well-known/openid-federation", timeout=10)
        metadata_token.raise_for_status()
        statement = jwt.decode(metadata_token.text, GSI_TEST_KEY, algorithms=["ES256"],
                               issuer=ISSUER, options={"require": ["iss", "sub", "exp", "iat"]})
        self.assertEqual(statement["sub"], ISSUER)
        provider = statement["metadata"]["openid_provider"]
        self.assertEqual(provider["issuer"], ISSUER)
        self.assertEqual(provider["pushed_authorization_request_endpoint"], f"{ISSUER}/PAR_Auth")
        self.assertEqual(provider["signed_jwks_uri"], f"{ISSUER}/jws.json")

        signed_jwks = requests.get(f"{SERVER}/jws.json", timeout=10)
        signed_jwks.raise_for_status()
        jwks_claims = jwt.decode(signed_jwks.text, GSI_TEST_KEY, algorithms=["ES256"], issuer=ISSUER,
                                 options={"require": ["iss", "iat"]})
        # The pinned reference emits no exp on its signed JWKS.
        self.assertLessEqual(time.time() - jwks_claims["iat"], 300)
        keys = jwks_claims["keys"]
        token_keys = [key for key in keys if key.get("kid") == "puk_fed_idp_token"]
        self.assertEqual(len(token_keys), 1)
        token_key = jwt.PyJWK.from_dict(token_keys[0], algorithm="ES256").key

        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        par_data = {
            "client_id": self.client_id,
            "state": state,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
            "code_challenge_method": "S256",
            "response_type": "code",
            "nonce": nonce,
            "scope": SCOPE,
            "acr_values": "gematik-ehealth-loa-high",
        }
        if FEDERATION_REMOTE:
            unknown = requests.post(f"{SERVER}/PAR_Auth", data={
                **par_data, "client_id": self.client_id + "/unknown",
            }, timeout=10)
            self.assertEqual(unknown.status_code, 400)
            self.assertIn("unknown", unknown.json()["error_description"].lower())
            type(self).serve_invalid = True
            try:
                invalid = requests.post(f"{SERVER}/PAR_Auth", data=par_data, timeout=10)
                self.assertEqual(invalid.status_code, 400, invalid.text)
                self.assertIn("signature", invalid.json()["error_description"].lower())
            finally:
                type(self).serve_invalid = False
        par = requests.post(f"{SERVER}/PAR_Auth", data=par_data, timeout=10)
        self.assertEqual(par.status_code, 201, par.text)
        request_uri = par.json()["request_uri"]
        landing = requests.get(f"{SERVER}/auth", params={
            "request_uri": request_uri, "client_id": self.client_id,
        }, timeout=10)
        self.assertEqual(landing.status_code, 200)
        self.assertIn("Test only", landing.text)

        auth = requests.get(f"{SERVER}/auth", params={
            "request_uri": request_uri,
            "user_id": KVNR,
            "selected_claims": "urn:telematik:claims:profession urn:telematik:claims:id",
        }, allow_redirects=False, timeout=10)
        self.assertEqual(auth.status_code, 302)
        target = urlsplit(auth.headers["Location"])
        self.assertEqual(f"{target.scheme}://{target.netloc}{target.path}", REDIRECT_URI)
        params = parse_qs(target.query)
        self.assertEqual(params["state"], [state])
        code = params["code"][0]
        token_request = {
            "grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": self.client_id, "redirect_uri": REDIRECT_URI,
        }
        wrong_verifier = requests.post(f"{SERVER}/token", data={
            **token_request, "code_verifier": secrets.token_urlsafe(48),
        }, timeout=10)
        self.assertEqual(wrong_verifier.status_code, 400)
        token_response = requests.post(f"{SERVER}/token", data=token_request, timeout=10)
        self.assertEqual(token_response.status_code, 200, token_response.text)
        result = token_response.json()
        self.assertEqual(result["access_token"], "TODO ACCESS_TOKEN")
        encrypted = jwe.JWE()
        encrypted.allowed_algs = ["ECDH-ES", "A256GCM"]
        encrypted.deserialize(result["id_token"], key=self.encryption_key)
        self.assertEqual(encrypted.jose_header["alg"], "ECDH-ES")
        self.assertEqual(encrypted.jose_header["enc"], "A256GCM")
        claims = validate_id_token(encrypted.payload, token_key, self.client_id, nonce)
        with self.assertRaises(ValueError):
            validate_id_token(encrypted.payload, token_key, self.client_id, "wrong-nonce")
        with self.assertRaises(jwt.InvalidAudienceError):
            validate_id_token(encrypted.payload, token_key, "http://invalid-client", nonce)
        with self.assertRaises(jwt.InvalidSignatureError):
            validate_id_token(encrypted.payload, ec.generate_private_key(ec.SECP256R1()).public_key(),
                              self.client_id, nonce)
        self.assertEqual(claims["urn:telematik:claims:id"], KVNR)
        self.assertEqual(claims["urn:telematik:claims:profession"], "1.2.276.0.76.4.49")
        self.assertNotEqual(claims["sub"], KVNR)
        replay = requests.post(f"{SERVER}/token", data=token_request, timeout=10)
        self.assertEqual(replay.status_code, 400)


if __name__ == "__main__":
    unittest.main()

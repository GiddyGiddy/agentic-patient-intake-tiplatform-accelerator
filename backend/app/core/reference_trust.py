"""Pinned trust-chain verification for the isolated gematik reference profile.

This is not a general OpenID Federation resolver or a production trust anchor.
"""

import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

FEDMASTER = "http://127.0.0.1:8083"
ISSUER = "http://127.0.0.1:8085"
FEDMASTER_KEY = b"""-----BEGIN PUBLIC KEY-----
MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAExJixkQv9D5ZR1JuqobDKcFfAcv0b
IJn8IQ4cLRwZuRqKYE65uvDtbP+bZDFmkwpsy/s5K8LBpHOy5tTFK39kDQ==
-----END PUBLIC KEY-----"""


class ReferenceTrustError(ValueError):
    """The reference trust chain or its metadata cannot be verified."""


@dataclass(frozen=True)
class ReferenceProvider:
    authorization_endpoint: str
    par_endpoint: str
    token_endpoint: str
    token_signing_key: Any


def _statement(token: str, key: Any, issuer: str, subject: str) -> dict:
    claims = jwt.decode(
        token, key, algorithms=["ES256"], issuer=issuer,
        options={"require": ["iss", "sub", "iat", "exp"]},
    )
    if claims["sub"] != subject:
        raise ReferenceTrustError("Unexpected entity subject")
    if any(field in claims for field in ("metadata_policy", "metadata_policy_crit", "constraints")):
        raise ReferenceTrustError("Unsupported federation policy or constraints")
    return claims


def _get(client: httpx.Client, url: str, params: dict | None = None) -> str:
    response = client.get(url, params=params, timeout=5.0, follow_redirects=False)
    response.raise_for_status()
    return response.text


def resolve_reference_provider(client: httpx.Client) -> ReferenceProvider:
    """Verify the pinned single-hop reference chain and return fixed provider endpoints."""
    try:
        root = _statement(
            _get(client, f"{FEDMASTER}/.well-known/openid-federation"),
            FEDMASTER_KEY, FEDMASTER, FEDMASTER,
        )
        fetch = root["metadata"]["federation_entity"]["federation_fetch_endpoint"]
        if fetch != f"{FEDMASTER}/federation_fetch_endpoint":
            raise ReferenceTrustError("Unexpected federation fetch endpoint")
        member = _statement(
            _get(client, fetch, {"iss": FEDMASTER, "sub": ISSUER}),
            FEDMASTER_KEY, FEDMASTER, ISSUER,
        )
        keys = [key for key in member["jwks"]["keys"] if key.get("kid") == "puk_idp_sig"]
        if len(keys) != 1 or keys[0].get("kty") != "EC" or keys[0].get("crv") != "P-256":
            raise ReferenceTrustError("Invalid reference IdP signing key")
        idp_key = jwt.PyJWK.from_dict(keys[0], algorithm="ES256").key
        idp = _statement(
            _get(client, f"{ISSUER}/.well-known/openid-federation"), idp_key, ISSUER, ISSUER,
        )
        hints = idp["authority_hints"]
        if not isinstance(hints, list) or FEDMASTER not in hints:
            raise ReferenceTrustError("Missing reference authority hint")
        provider = idp["metadata"]["openid_provider"]
        expected = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/auth",
            "pushed_authorization_request_endpoint": f"{ISSUER}/PAR_Auth",
            "token_endpoint": f"{ISSUER}/token",
            "signed_jwks_uri": f"{ISSUER}/jws.json",
        }
        if any(provider.get(name) != value for name, value in expected.items()):
            raise ReferenceTrustError("Unexpected reference provider metadata")
        signed_jwks = jwt.decode(
            _get(client, expected["signed_jwks_uri"]), idp_key,
            algorithms=["ES256"], issuer=ISSUER, options={"require": ["iss", "iat"]},
        )
        # This pinned reference implementation does not emit exp for signed JWKS.
        if time.time() - signed_jwks["iat"] > 300:
            raise ReferenceTrustError("Reference signed JWKS is stale")
        token_keys = [key for key in signed_jwks["keys"] if key.get("kid") == "puk_fed_idp_token"]
        if len(token_keys) != 1 or token_keys[0].get("kty") != "EC" or token_keys[0].get("crv") != "P-256":
            raise ReferenceTrustError("Invalid reference token signing key")
        return ReferenceProvider(
            authorization_endpoint=expected["authorization_endpoint"],
            par_endpoint=expected["pushed_authorization_request_endpoint"],
            token_endpoint=expected["token_endpoint"],
            token_signing_key=jwt.PyJWK.from_dict(token_keys[0], algorithm="ES256").key,
        )
    except ReferenceTrustError:
        raise
    except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, IndexError, ValueError) as exc:
        raise ReferenceTrustError("Reference federation verification failed") from exc

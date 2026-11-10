import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

from app.core import reference_trust as trust


@pytest.fixture
def federation(monkeypatch):
    root = ec.generate_private_key(ec.SECP256R1())
    idp = ec.generate_private_key(ec.SECP256R1())
    token = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(
        trust, "FEDMASTER_KEY",
        root.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )
    now = int(time.time())

    def key(k, kid):
        return {**json.loads(ECAlgorithm.to_jwk(k.public_key())), "kid": kid}

    claims = {
        "root": {
            "iss": trust.FEDMASTER, "sub": trust.FEDMASTER, "iat": now, "exp": now + 60,
            "metadata": {"federation_entity": {
                "federation_fetch_endpoint": f"{trust.FEDMASTER}/federation_fetch_endpoint",
            }},
        },
        "member": {
            "iss": trust.FEDMASTER, "sub": trust.ISSUER, "iat": now, "exp": now + 60,
            "jwks": {"keys": [key(idp, "puk_idp_sig")]},
        },
        "idp": {
            "iss": trust.ISSUER, "sub": trust.ISSUER, "iat": now, "exp": now + 60,
            "authority_hints": [trust.FEDMASTER],
            "metadata": {"openid_provider": {
                "issuer": trust.ISSUER,
                "authorization_endpoint": f"{trust.ISSUER}/auth",
                "pushed_authorization_request_endpoint": f"{trust.ISSUER}/PAR_Auth",
                "token_endpoint": f"{trust.ISSUER}/token",
                "signed_jwks_uri": f"{trust.ISSUER}/jws.json",
            }},
        },
        "jwks": {"iss": trust.ISSUER, "iat": now, "keys": [key(token, "puk_fed_idp_token")]},
    }

    def client():
        def handler(request):
            route = {
                f"{trust.FEDMASTER}/.well-known/openid-federation": ("root", root),
                f"{trust.FEDMASTER}/federation_fetch_endpoint": ("member", root),
                f"{trust.ISSUER}/.well-known/openid-federation": ("idp", idp),
                f"{trust.ISSUER}/jws.json": ("jwks", idp),
            }
            name, signing_key = route[str(request.url.copy_with(query=None))]
            if name == "member":
                assert dict(request.url.params) == {"iss": trust.FEDMASTER, "sub": trust.ISSUER}
            return httpx.Response(200, text=jwt.encode(claims[name], signing_key, algorithm="ES256"))
        return httpx.Client(transport=httpx.MockTransport(handler))

    return claims, token, client


def test_reference_chain_returns_verified_token_key(federation):
    _, token, client = federation
    with client() as http:
        provider = trust.resolve_reference_provider(http)
    assert provider.par_endpoint == f"{trust.ISSUER}/PAR_Auth"
    assert provider.token_signing_key.public_numbers() == token.public_key().public_numbers()


@pytest.mark.parametrize("mutation", [
    lambda c: c["member"].update(sub="http://wrong"),
    lambda c: c["member"].update(exp=int(time.time()) - 1),
    lambda c: c["member"].update(metadata_policy={}),
    lambda c: c["idp"].update(constraints={}),
    lambda c: c["idp"].update(authority_hints=[]),
    lambda c: c["idp"]["metadata"]["openid_provider"].update(token_endpoint="http://attacker/token"),
    lambda c: c["root"]["metadata"]["federation_entity"].update(federation_fetch_endpoint="http://attacker"),
    lambda c: c["jwks"].update(iat=int(time.time()) - 301),
    lambda c: c["jwks"].update(keys=[]),
])
def test_reference_chain_rejects_invalid_data(federation, mutation):
    claims, _, client = federation
    mutation(claims)
    with client() as http, pytest.raises(trust.ReferenceTrustError):
        trust.resolve_reference_provider(http)


def test_reference_chain_rejects_bad_signatures(federation, monkeypatch):
    _, _, client = federation
    wrong = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(trust, "FEDMASTER_KEY", wrong.public_key())
    with client() as http, pytest.raises(trust.ReferenceTrustError):
        trust.resolve_reference_provider(http)

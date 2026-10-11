import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from searchos.core import security
from searchos.core.config import Settings

SECRET = "t" * 40


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/d", jwt_secret=SECRET, **kw)  # type: ignore[arg-type]


def test_passwords_use_argon2id() -> None:
    hashed = security.hash_password("a long enough password")
    assert hashed.startswith("$argon2id$")
    assert security.verify_password(hashed, "a long enough password")
    assert not security.verify_password(hashed, "something else")
    assert not security.verify_password("not-a-hash", "x")


def test_hashes_are_salted() -> None:
    assert security.hash_password("same password!!") != security.hash_password("same password!!")


def test_access_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = security.create_access_token(user_id, settings())
    assert security.decode_access_token(token, settings()) == user_id


def test_access_token_expires() -> None:
    old = datetime.now(UTC) - timedelta(minutes=16)
    token = security.create_access_token(uuid.uuid4(), settings(), now=old)
    with pytest.raises(security.InvalidAccessTokenError):
        security.decode_access_token(token, settings())


def test_access_token_ttl_is_15_minutes_by_default() -> None:
    token = security.create_access_token(uuid.uuid4(), settings())
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 15 * 60


def test_access_token_wrong_secret_and_none_alg_rejected() -> None:
    user_id = uuid.uuid4()
    forged = jwt.encode({"sub": str(user_id), "typ": "access", "iat": 1, "exp": 9999999999}, "x" * 40)
    with pytest.raises(security.InvalidAccessTokenError):
        security.decode_access_token(forged, settings())
    unsigned = jwt.encode(
        {"sub": str(user_id), "typ": "access", "iat": 1, "exp": 9999999999}, None, algorithm="none"
    )
    with pytest.raises(security.InvalidAccessTokenError):
        security.decode_access_token(unsigned, settings())


def test_wrong_token_type_rejected() -> None:
    token = jwt.encode({"sub": str(uuid.uuid4()), "typ": "refresh", "iat": 1, "exp": 9999999999}, SECRET)
    with pytest.raises(security.InvalidAccessTokenError):
        security.decode_access_token(token, settings())


def test_refresh_tokens_are_random_and_hashed() -> None:
    a, b = security.new_refresh_token(), security.new_refresh_token()
    assert a != b
    assert len(a) >= 64
    assert security.hash_refresh_token(a) != a
    assert len(security.hash_refresh_token(a)) == 64

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError):
        return False


def make_token(user_id: UUID, token_type: str, token_id: UUID | None = None) -> str:
    now = datetime.now(UTC)
    lifetime = (
        timedelta(minutes=settings.access_token_minutes)
        if token_type == "access"
        else timedelta(days=settings.refresh_token_days)
    )
    claims = {
        "sub": str(user_id),
        "type": token_type,
        "jti": str(token_id or uuid4()),
        "iat": now,
        "exp": now + lifetime,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, token_type: str) -> tuple[UUID, UUID]:
    claims = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "type", "jti", "iat", "exp"]},
    )
    if claims["type"] != token_type:
        raise jwt.InvalidTokenError("Wrong token type")
    try:
        return UUID(claims["sub"]), UUID(claims["jti"])
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("Invalid token identity") from exc

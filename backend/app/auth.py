from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_token, hash_password, make_token, verify_password
from app.database import get_session
from app.models import RefreshToken, User
from app.schemas.auth import Credentials, LoginRequest, RefreshRequest, TokenPair, UserResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
Session = Annotated[AsyncSession, Depends(get_session)]


def auth_error(detail: str = "Invalid credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"detail": detail, "code": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


async def issue_tokens(session: AsyncSession, user: User) -> TokenPair:
    refresh = RefreshToken(
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    session.add(refresh)
    await session.flush()
    return TokenPair(
        access_token=make_token(user.id, "access"),
        refresh_token=make_token(user.id, "refresh", refresh.id),
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(body: Credentials, session: Session) -> User:
    password_hash = await run_in_threadpool(hash_password, body.password)
    user = User(email=str(body.email).lower(), password_hash=password_hash)
    try:
        async with session.begin():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"detail": "Email already registered", "code": "email_taken"},
        ) from exc
    return user


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, session: Session) -> TokenPair:
    user = await session.scalar(select(User).where(User.email == str(body.email).lower()))
    if user is None or not await run_in_threadpool(
        verify_password, body.password, user.password_hash
    ):
        raise auth_error()
    tokens = await issue_tokens(session, user)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: Session) -> TokenPair:
    try:
        user_id, token_id = decode_token(body.refresh_token, "refresh")
    except jwt.InvalidTokenError as exc:
        raise auth_error("Invalid refresh token") from exc

    async with session.begin():
        record = await session.scalar(
            select(RefreshToken).where(RefreshToken.id == token_id).with_for_update()
        )
        now = datetime.now(UTC)
        if (
            record is None
            or record.user_id != user_id
            or record.revoked_at is not None
            or record.expires_at <= now
        ):
            raise auth_error("Invalid refresh token")
        user = await session.get(User, user_id)
        if user is None:
            raise auth_error("Invalid refresh token")
        record.revoked_at = now
        tokens = await issue_tokens(session, user)
    return tokens


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Session,
) -> User:
    if credentials is None:
        raise auth_error("Bearer token required")
    try:
        user_id, _ = decode_token(credentials.credentials, "access")
    except jwt.InvalidTokenError as exc:
        raise auth_error("Invalid access token") from exc
    user = await session.get(User, user_id)
    if user is None:
        raise auth_error("Invalid access token")
    return user


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user

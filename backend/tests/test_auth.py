import os
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import decode_token, hash_password, make_token, verify_password
from app.database import get_session
from app.main import app


def test_password_hash_and_token_types() -> None:
    user_id = uuid4()
    password_hash = hash_password("strong-password-123")
    assert password_hash != "strong-password-123"
    assert verify_password("strong-password-123", password_hash)
    assert not verify_password("wrong-password", password_hash)
    assert not verify_password("wrong-password", "invalid-hash")
    access_token = make_token(user_id, "access")
    assert decode_token(access_token, "access")[0] == user_id
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(access_token, "refresh")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(f"{access_token}tampered", "access")


@pytest.fixture
async def client():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a migrated PostgreSQL test database")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            sessions = async_sessionmaker(
                connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )

            async def override_session():
                async with sessions() as session:
                    yield session

            app.dependency_overrides[get_session] = override_session
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as http_client:
                    yield http_client
            finally:
                app.dependency_overrides.clear()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_register_login_refresh_and_protected_route(client: AsyncClient) -> None:
    email = f"{uuid4()}@example.com"
    credentials = {"email": email, "password": "strong-password-123"}

    response = await client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201
    assert response.json()["email"] == email
    assert "password_hash" not in response.json()
    assert (await client.post("/api/v1/auth/register", json=credentials)).status_code == 409

    assert (await client.get("/api/v1/auth/me")).status_code == 401
    wrong = await client.post(
        "/api/v1/auth/login", json={**credentials, "password": "wrong-password"}
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "unauthorized"

    response = await client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    tokens = response.json()
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert (
        await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
        )
    ).status_code == 401

    refreshed = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401

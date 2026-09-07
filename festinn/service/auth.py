"""FestIn service authentication: JWT tokens + bcrypt passwords."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from passlib.context import CryptContext
except ImportError:
    CryptContext = None  # type: ignore

try:
    from jose import JWTError, jwt
except ImportError:
    jwt = None  # type: ignore
    JWTError = Exception  # type: ignore


SECRET_DEFAULT = "festin-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


class PasswordHasher:
    """Sync bcrypt hashing — fast enough for async context."""

    def __init__(self) -> None:
        if CryptContext is None:
            raise ImportError("passlib[bcrypt] not installed. pip install passlib[bcrypt]")
        self._ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

    def hash_password(self, password: str) -> str:
        return self._ctx.hash(password)

    def verify_password(self, password: str, hashed: str) -> bool:
        return self._ctx.verify(password, hashed)


class TokenService:
    """JWT access token creation and verification."""

    def __init__(self, secret_key: str = SECRET_DEFAULT,
                 expires_minutes: int = 60) -> None:
        if jwt is None:
            raise ImportError("python-jose[cryptography] not installed")
        self._secret_key = secret_key
        self._algorithm = ALGORITHM
        self._expires_minutes = expires_minutes

    def create_token(self, username: str) -> str:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=self._expires_minutes)
        payload = {
            "sub": username,
            "exp": expire.timestamp(),
            "iat": datetime.now(timezone.utc).timestamp(),
        }
        return jwt.encode(payload, self._secret_key,
                            algorithm=self._algorithm)

    def verify_token(self, token: str) -> str | None:
        try:
            payload = jwt.decode(token, self._secret_key,
                                 [self._algorithm])
            return payload.get("sub")
        except Exception:
            return None


class AuthService:
    """Service layer combining user management + auth."""

    def __init__(self, database: Any,
                 secret_key: str = SECRET_DEFAULT) -> None:
        self._db = database
        self._hasher = PasswordHasher()
        self._token_service = TokenService(
            secret_key=secret_key)

    async def init_admin(self, username: str = "admin",
                         password: str | None = None) -> bool:
        if await self._db.user_exists():
            return False
        pw = password or secrets.token_urlsafe(12)
        pw_hash = self._hasher.hash_password(pw)
        await self._db.create_user(username, pw_hash)
        print(f"[auth] Admin: {username} (password: {pw})")
        return True

    async def login(self, username: str, password: str) -> dict[str, Any]:
        user = await self._db.get_user_by_username(username)
        if user is None:
            raise ValueError("Invalid credentials")
        if not self._hasher.verify_password(password, user["pw_hash"]):
            raise ValueError("Invalid credentials")
        token = self._token_service.create_token(username)
        return {"access_token": token, "token_type": "bearer"}

    async def verify(self, token: str) -> dict[str, Any] | None:
        username = self._token_service.verify_token(token)
        if username is None:
            return None
        user = await self._db.get_user_by_username(username)
        if user is None:
            return None
        return {"user_id": user["id"], "username": username,
                "role": user.get("role", "user")}

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        row = await self._db.get_user(user_id)
        if row is None:
            return None
        return {"id": row["id"], "username": row["username"],
                "role": row["role"]}

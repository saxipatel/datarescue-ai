import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings
from app.errors import AuthenticationError

ALGORITHM = "HS256"


def create_access_token(user_id: uuid.UUID, role: str) -> tuple[str, datetime]:
    settings = get_settings()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_ttl_minutes)
    claims = {"sub": str(user_id), "role": role, "exp": expires_at, "iat": datetime.now(UTC)}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM), expires_at


def decode_access_token(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid or expired token") from exc

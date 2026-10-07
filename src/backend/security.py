from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.const import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM

oauth2_password = OAuth2PasswordBearer(tokenUrl="token", scopes={})

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class Token(BaseModel):
    access_token: str = Field(alias="access_token")
    token_type: Optional[str] = Field(alias="token_type", default=None)


class TokenModel(BaseModel):
    sub: str


def process_get_token(username: str, password: str, grant_type: str) -> Token:
    if not (authenticate_user(username, password) and grant_type == "password"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(username)
    return Token(access_token=access_token, token_type="bearer")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def _signing_key() -> str:
    key = get_settings().jwt_secret_key
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password login is not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return key.get_secret_value()


def authenticate_user(username: str, password: str) -> bool:
    settings = get_settings()
    if not settings.password_login_enabled:
        return False
    assert settings.auth_password_hash is not None
    return username == settings.auth_user and verify_password(password, settings.auth_password_hash.get_secret_value())


def create_access_token(username: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": username, "exp": expires}, _signing_key(), algorithm=ALGORITHM)


def validate_token(token: str) -> str:
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if not username:
            raise ValueError("Token payload missing 'sub'")
        return username
    except (JWTError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Could not validate credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_token_oauth2_password_bearer(
    security_scopes: SecurityScopes,
    token: str = Depends(oauth2_password),
) -> TokenModel:
    """
    Validate and decode token.

    :param security_scopes: SecurityScopes required for validation
    :param token: Token provided by Authorization header
    :return: Decoded token information or Raise HTTPException if token is invalid
    """
    username = validate_token(token)
    return TokenModel(sub=username)


def validate_scope_oauth2_password_bearer(required_scopes: SecurityScopes, token_scopes: List[str]) -> bool:
    """
    Validate required scopes are included in token scope.

    :param required_scopes: Required scope to access called API
    :param token_scopes: Scope present in token
    :return: True if access to called API is allowed
    """
    return set(required_scopes.scopes).issubset(set(token_scopes))

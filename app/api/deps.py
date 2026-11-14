"""Autenticação do mecânico/operador: JWT emitido pelo OS Service (role=admin),
validado com o segredo compartilhado (o API Gateway também valida)."""

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.config import get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class Operador(BaseModel):
    username: str


def get_operador(token: str = Depends(oauth2_scheme)) -> Operador:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    if not payload.get("sub"):
        raise HTTPException(status_code=401, detail="Token inválido")
    if payload.get("role", "admin") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito à oficina")
    return Operador(username=str(payload["sub"]))

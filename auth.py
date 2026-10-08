"""
auth.py
تشفير الباسورد، إنشاء وفك تشفير JWT، ونظام الصلاحيات (RBAC) اللي بيتأكد إن
كل نوع مستخدم يقدر يوصل بس للـ endpoints المسموح له بيها.

ملاحظة أمان مهمة: SECRET_KEY هنا Placeholder للتطوير فقط - لازم تتغير
بمتغير بيئة حقيقي (env var) قبل أي نشر فعلي (production).
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Iterable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from database import get_db

SECRET_KEY = os.environ.get("MEDPLATFORM_SECRET_KEY", "dev-secret-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # أسبوع

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(user_id: int, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="انتهت صلاحية الجلسة، سجل دخول تاني")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="بيانات الدخول غير صالحة")


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> dict:
    """
    يرجّع dict فيه بيانات المستخدم الحالي (id, role, ...) بعد التأكد من الـ token
    ومن إن الحساب لسه فعّال (is_active) في قاعدة البيانات.
    """
    payload = decode_access_token(creds.credentials)
    user_id = int(payload["sub"])

    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, phone, role, is_active, country, city "
            "FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if row is None or not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                             detail="الحساب غير موجود أو موقوف")

    return dict(row)


def require_role(allowed_roles: Iterable[str]):
    """
    Dependency factory لعمل RBAC: بيتحقق إن دور المستخدم الحالي ضمن الأدوار
    المسموح لها بالوصول للـ endpoint ده. الاستخدام:
        @router.get("/x")
        def x(user: dict = Depends(require_role(["doctor", "secretary"]))): ...
    """
    allowed = set(allowed_roles)

    def checker(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"هذا الإجراء متاح فقط لـ: {', '.join(allowed)}",
            )
        return user

    return checker

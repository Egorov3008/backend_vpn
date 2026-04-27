"""Repository classes for typed database access."""
from .users import UserRepository
from .login_codes import LoginCodeRepository

__all__ = [
    "UserRepository",
    "LoginCodeRepository",
]

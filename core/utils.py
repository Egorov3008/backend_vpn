import inspect
import random
import string
from functools import wraps
from typing import Callable, Any


def filter_by_method_signature(method: Callable) -> Callable:
    """Decorator that filters kwargs to only those present in method's signature."""

    @wraps(method)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        sig = inspect.signature(method)
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return method(*args, **filtered_kwargs)

    return wrapper


def generate_random_email(length: int = 6) -> str:
    """Generate random email with specified length."""
    random_str = ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))
    return f"{random_str}@vpn.local"


def sorted_keys(data: list) -> dict:
    """Group a flat list of keys into chunks of 6."""
    result: dict = {}
    group: list = []
    group_num = 1
    for key in data:
        if len(group) < 6:
            group.append(key)
        else:
            result[group_num] = group
            group_num += 1
            group = [key]
    if group:
        result[group_num] = group
    return result


def generate_login_code(length: int = 8) -> str:
    """Generate random 8-character alphanumeric login code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

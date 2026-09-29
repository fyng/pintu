"""Optional helpers for pintu recipes (SPEC §8)."""

from __future__ import annotations

import pickle
from typing import Any, Callable

from .save import save

__all__ = ["cache", "cache_clear", "save"]

_memo: dict = {}


def _key(fn: Callable, args: tuple, kwargs: dict) -> Any:
    name = (getattr(fn, "__module__", None), getattr(fn, "__qualname__", repr(fn)))
    try:
        return name, hash((args, tuple(sorted(kwargs.items()))))
    except TypeError:
        return name, pickle.dumps((args, sorted(kwargs.items())))


def cache(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    """Calls ``fn(*args, **kwargs)`` once per process and returns the kept result.

    The key is the function's module and qualified name plus the arguments, so it
    survives a reload of the recipe module. Arguments must be hashable or picklable.
    """
    k = _key(fn, args, kwargs)
    if k not in _memo:
        _memo[k] = fn(*args, **kwargs)
    return _memo[k]


def cache_clear() -> None:
    """Drops every kept result."""
    _memo.clear()

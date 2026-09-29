"""Optional helpers for pintu recipes (SPEC §8)."""

from __future__ import annotations

import pickle
from typing import Any, Callable, Optional

from .multiples import choice, multiples
from .save import save

__all__ = ["cache", "cache_clear", "choice", "multiples", "panel", "save"]

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


def panel(min_size: Optional[tuple[float, float]] = None, max_size: Optional[tuple[float, float]] = None,
          params: Optional[dict] = None) -> Callable:
    """Marks a recipe ``fn(w, h, **params)`` with its size range and param choices.

    pintu reads ``min_size`` and ``max_size`` from the decorator with ``ast``, so
    they must be literal ``(w, h)`` tuples in mm. Outside the range the panel shows
    a size badge and "Adapt to size". The function itself is returned unchanged.

    Args:
        min_size: Smallest (w, h) in mm the recipe is designed for.
        max_size: Largest (w, h) in mm the recipe is designed for.
        params: Param name → ``choice`` of its values.
    """

    def wrap(fn: Callable) -> Callable:
        fn.__pintu_panel__ = {"min_size": min_size, "max_size": max_size, "params": params or {}}
        return fn

    return wrap

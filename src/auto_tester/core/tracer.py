"""
Step tracing — the "light hooks" that give the tester visibility *inside* a
multi-step pipeline.

Three ways to capture steps, all feeding the same active :class:`Trace`:

  1. ``@step`` decorator           — annotate functions you own.
  2. ``instrument([...])``         — monkeypatch a list of dotted paths with no
                                     source edits (used for third-party targets
                                     like jeevn).
  3. (log/artifact tap lives in the adapter, which reads files into RunResult.logs)

A run wraps execution in ``trace_context()``; any traced call that fires while
that context is active is appended to its Trace. Captured args/results are made
JSON-safe and size-bounded by :func:`safe_value` so large rasters/frames don't
blow up the trace.
"""

from __future__ import annotations

import contextvars
import functools
import importlib
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, List, Tuple

from .models import StepRecord, Trace

# The trace currently being recorded (None when no run is active).
_active: "contextvars.ContextVar[Trace | None]" = contextvars.ContextVar(
    "auto_tester_active_trace", default=None
)

_MAX_STR = 2000
_MAX_ITEMS = 50


def safe_value(value: Any, _depth: int = 0) -> Any:
    """Convert an arbitrary value into a small, JSON-serializable summary.

    Keeps primitives and short containers verbatim; summarizes big or exotic
    objects (numpy arrays, dataframes, custom classes) by shape/type/repr so the
    trace stays readable and serializable.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + f"...(+{len(value) - _MAX_STR} chars)"
    if _depth >= 6:
        return f"<max depth: {type(value).__name__}>"

    # numpy array / anything with shape+dtype — summarize, don't dump
    shape = getattr(value, "shape", None)
    dtype = getattr(value, "dtype", None)
    if shape is not None and dtype is not None:
        try:
            return {"__array__": True, "shape": list(shape), "dtype": str(dtype)}
        except Exception:
            return f"<array {type(value).__name__}>"

    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["__truncated__"] = f"+{len(value) - _MAX_ITEMS} more keys"
                break
            out[str(k)] = safe_value(v, _depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
        out_list = [safe_value(v, _depth + 1) for v in seq[:_MAX_ITEMS]]
        if len(seq) > _MAX_ITEMS:
            out_list.append(f"...(+{len(seq) - _MAX_ITEMS} more items)")
        return out_list

    # Fallback: short repr
    try:
        r = repr(value)
    except Exception:
        r = f"<unreprable {type(value).__name__}>"
    return r if len(r) <= _MAX_STR else r[:_MAX_STR] + "..."


@contextmanager
def trace_context():
    """Activate a fresh Trace for the duration of the block; yields it."""
    trace = Trace()
    token = _active.set(trace)
    try:
        yield trace
    finally:
        _active.reset(token)


def _record(name: str, args: Dict[str, Any], started: float, result: Any, error: str | None):
    trace = _active.get()
    if trace is None:
        return  # not inside a run; nothing to record
    trace.add(
        StepRecord(
            name=name,
            args=safe_value(args),
            result=safe_value(result),
            started_at=started,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            error=error,
        )
    )


def _wrap(func: Callable, name: str) -> Callable:
    @functools.wraps(func)
    def wrapper(*a, **kw):
        if _active.get() is None:  # zero overhead when not tracing
            return func(*a, **kw)
        started = time.perf_counter()
        captured = _capture_args(func, a, kw)
        try:
            result = func(*a, **kw)
        except Exception:
            _record(name, captured, started, None, traceback.format_exc())
            raise
        _record(name, captured, started, result, None)
        return result

    wrapper.__auto_tester_wrapped__ = True  # type: ignore[attr-defined]
    wrapper.__auto_tester_orig__ = func  # type: ignore[attr-defined]
    return wrapper


def _capture_args(func: Callable, a: Tuple, kw: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort bind of positional+keyword args to parameter names."""
    try:
        import inspect

        sig = inspect.signature(func)
        bound = sig.bind_partial(*a, **kw)
        return {k: safe_value(v) for k, v in bound.arguments.items()}
    except Exception:
        return {"args": safe_value(list(a)), "kwargs": safe_value(kw)}


def step(name: str | None = None) -> Callable:
    """Decorator: record each call of the wrapped function as a trace step."""

    def deco(func: Callable) -> Callable:
        step_name = name or f"{func.__module__}.{func.__qualname__}"
        return _wrap(func, step_name)

    return deco


def _resolve(path: str) -> Tuple[Any, str, str]:
    """Resolve a dotted target into (owner_object, attr_name, display_name).

    Accepts ``pkg.module:func``, ``pkg.module.func``, or
    ``pkg.module.Class.method``. Returns the object that *holds* the attribute
    so we can monkeypatch it.
    """
    if ":" in path:
        module_name, attr_path = path.split(":", 1)
    else:
        # split at the last importable module boundary
        parts = path.split(".")
        module_name, attr_path = None, None
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            try:
                importlib.import_module(candidate)
                module_name = candidate
                attr_path = ".".join(parts[i:])
                break
            except Exception:
                continue
        if not module_name or not attr_path:
            raise ImportError(f"Could not resolve target path: {path}")

    obj: Any = importlib.import_module(module_name)
    attrs = attr_path.split(".")
    for a in attrs[:-1]:
        obj = getattr(obj, a)
    return obj, attrs[-1], f"{module_name}.{attr_path}"


@contextmanager
def instrument(targets: Iterable[str]):
    """Monkeypatch each dotted ``target`` with a tracing wrapper for the block.

    Restores the originals on exit. Targets that fail to resolve are skipped
    (recorded in the returned list's absence) rather than aborting the run.
    """
    patched: List[Tuple[Any, str, Any]] = []
    try:
        for path in targets:
            try:
                owner, attr, display = _resolve(path)
            except Exception:
                continue
            orig = getattr(owner, attr)
            if getattr(orig, "__auto_tester_wrapped__", False):
                continue
            # staticmethod/classmethod stored on classes need unwrapping care;
            # getattr already returns the callable, so wrap that.
            setattr(owner, attr, _wrap(orig, display))
            patched.append((owner, attr, orig))
        yield
    finally:
        for owner, attr, orig in patched:
            setattr(owner, attr, orig)

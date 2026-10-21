"""Research-support inspection primitives."""

from ._common import SupportError, dump_new, fingerprint, load_json, resolve_path, utc_now


def __getattr__(name: str):
    # Keep ``python -m scripts.research_support.handoff`` free of runpy's
    # "already in sys.modules" warning while retaining the convenient package
    # level import for library consumers.
    if name == "inspect_handoff":
        from .handoff import inspect_handoff

        return inspect_handoff
    raise AttributeError(name)

__all__ = [
    "SupportError",
    "dump_new",
    "fingerprint",
    "inspect_handoff",
    "load_json",
    "resolve_path",
    "utc_now",
]

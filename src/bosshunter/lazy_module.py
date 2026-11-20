"""Defer heavy third-party imports until first use (v30 startup speed).

Why a proxy, not module ``__getattr__`` (PEP 562): ``__getattr__`` only
fires on *attribute* access (``mod.httpx``), never on the global-name
lookup a function body does (``httpx.get(...)`` raises NameError until
something else touched the attribute first). A proxy object assigned
to the module global *is* a real global, so function bodies work, and
the real import only happens on first attribute access.

Why a proxy, not a function-local import: the test suite patches
``<module>.httpx.get`` / ``<module>.PdfReader`` in dozens of places.
With a proxy those patches keep working: mock sets the attribute on
the proxy, the function sees the mock, and patch exit deletes it again
(mock restores non-local attributes with delattr, after which the
proxy falls back to the real module). A function-local import would
break every one of those patch sites.

Only importlib is imported here, so this module is safe to import
from anywhere, including import-time code.
"""

import importlib


class _LazyModule:
    """Proxy for a whole module, imported on first attribute access."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_lazy_name", name)
        object.__setattr__(self, "_lazy_module", None)

    def _load(self):
        module = object.__getattribute__(self, "_lazy_module")
        if module is None:
            module = importlib.import_module(object.__getattribute__(self, "_lazy_name"))
            object.__setattr__(self, "_lazy_module", module)
        return module

    def __getattr__(self, name: str):
        if name.startswith("_lazy"):
            raise AttributeError(name)
        return getattr(self._load(), name)


class _LazyAttr:
    """Proxy for one attribute of a heavy module (e.g. pypdf.PdfReader).

    Only call/attribute use loads the real module; the proxy itself is
    callable so ``PdfReader(...)`` works unchanged.
    """

    def __init__(self, module_name: str, attr_name: str) -> None:
        object.__setattr__(self, "_lazy_module_name", module_name)
        object.__setattr__(self, "_lazy_attr_name", attr_name)
        object.__setattr__(self, "_lazy_value", None)

    def _load(self):
        value = object.__getattribute__(self, "_lazy_value")
        if value is None:
            module = importlib.import_module(object.__getattribute__(self, "_lazy_module_name"))
            value = getattr(module, object.__getattribute__(self, "_lazy_attr_name"))
            object.__setattr__(self, "_lazy_value", value)
        return value

    def __call__(self, *args, **kwargs):
        return self._load()(*args, **kwargs)

    def __getattr__(self, name: str):
        if name.startswith("_lazy"):
            raise AttributeError(name)
        return getattr(self._load(), name)


def lazy_module(name: str) -> _LazyModule:
    """Return a proxy for the module ``name``, imported on first use."""
    return _LazyModule(name)


def lazy_attr(module_name: str, attr_name: str) -> _LazyAttr:
    """Return a proxy for ``getattr(module, attr)``, resolved on first use."""
    return _LazyAttr(module_name, attr_name)

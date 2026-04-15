import importlib
import sys
import types
from contextlib import contextmanager


def make_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


@contextmanager
def stubbed_modules(mapping: dict[str, types.ModuleType]):
    sentinel = object()
    originals = {}
    try:
        for name, module in mapping.items():
            originals[name] = sys.modules.get(name, sentinel)
            sys.modules[name] = module
        yield
    finally:
        for name, original in originals.items():
            if original is sentinel:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def import_fresh(module_name: str, stubs: dict[str, types.ModuleType] | None = None):
    stubs = stubs or {}
    with stubbed_modules(stubs):
        sys.modules.pop(module_name, None)
        parent_name, _, child_name = module_name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, child_name):
            delattr(parent, child_name)
        return importlib.import_module(module_name)


def make_sqlalchemy_stubs():
    orm = make_module("sqlalchemy.orm", Session=type("Session", (), {}))
    sqlalchemy = make_module("sqlalchemy", orm=orm)
    return {
        "sqlalchemy": sqlalchemy,
        "sqlalchemy.orm": orm,
    }


class FakeField:
    def __init__(self, name: str):
        self.name = name

    def __eq__(self, other):
        return ("eq", self.name, other)

    def in_(self, values):
        return ("in", self.name, tuple(values))

    def desc(self):
        return ("desc", self.name)


def make_model_class(name: str, field_names: list[str]):
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    attrs = {"__init__": __init__}
    for field_name in field_names:
        attrs[field_name] = FakeField(field_name)
    return type(name, (), attrs)


class FakeQuery:
    def __init__(self, first_result=None, all_result=None):
        self.first_result = first_result
        self.all_result = list(all_result or [])
        self.filters = []
        self.orderings = []
        self.limit_value = None

    def filter(self, *args):
        self.filters.extend(args)
        return self

    def order_by(self, *args):
        self.orderings.extend(args)
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def first(self):
        return self.first_result

    def all(self):
        return list(self.all_result)


class FakeSession:
    def __init__(self, query_map=None):
        self.query_map = dict(query_map or {})
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, obj):
        self.refreshed.append(obj)

    def query(self, model):
        return self.query_map.setdefault(model, FakeQuery())


@contextmanager
def patch_attr(obj, name: str, value):
    sentinel = object()
    original = getattr(obj, name, sentinel)
    setattr(obj, name, value)
    try:
        yield value
    finally:
        if original is sentinel:
            delattr(obj, name)
        else:
            setattr(obj, name, original)


class Spy:
    def __init__(self, return_value=None, side_effect=None):
        self.return_value = return_value
        self.side_effect = side_effect
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.side_effect is not None:
            return self.side_effect(*args, **kwargs)
        return self.return_value

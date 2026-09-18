"""Guard against the bug that froze hamdash on Windows.

pywebview introspects a js_api object to build its JavaScript proxy, and on
the winforms backend that walk follows public attributes. A pywebview Window
stored on the api object leads the walk into .NET's object graph
(Window.native -> Form.AccessibilityObject.Bounds -> Rectangle.Empty ->
Rectangle.Empty -> ...), which never terminates.

Run with:  python -m unittest discover -s tests
"""
import unittest

from hamdash.windows import PopoutApi


def public_attrs(obj):
    return [n for n in dir(obj) if not n.startswith("_")]


def walk(obj, depth=0, limit=6, seen=None):
    """Follow public attributes the way pywebview's serialiser does, and
    report the deepest chain found. A cyclic property graph hits the limit."""
    if depth >= limit:
        return depth
    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return depth
    seen.add(id(obj))
    deepest = depth
    for name in public_attrs(obj):
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if callable(value) or isinstance(value, (str, int, float, bool, type(None))):
            continue
        deepest = max(deepest, walk(value, depth + 1, limit, seen))
    return deepest


class SelfReferential:
    """Stands in for .NET's Rectangle.Empty - the shape that caused the hang."""

    @property
    def Empty(self):
        return SelfReferential()


class TestJsApiShape(unittest.TestCase):
    def test_api_exposes_methods_only(self):
        api = PopoutApi()
        for name in public_attrs(api):
            self.assertTrue(
                callable(getattr(api, name)),
                f"PopoutApi.{name} is data, not a method. pywebview will walk "
                f"it when building the JS proxy; keep state in module globals.")

    def test_api_is_shallow(self):
        self.assertEqual(walk(PopoutApi()), 0,
                         "something on PopoutApi leads the introspection walk "
                         "deeper than itself")

    def test_walk_would_catch_the_old_bug(self):
        class Bad(PopoutApi):
            def __init__(self):
                self.main = SelfReferential()   # what the old code did

        self.assertGreaterEqual(walk(Bad()), 6,
                                "the guard itself is broken: it failed to "
                                "notice a runaway attribute chain")


if __name__ == "__main__":
    unittest.main()

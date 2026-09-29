from pintu_sdk import cache, cache_clear


def test_cache_memoizes_by_args():
    calls = []

    def load(n, scale=1):
        calls.append(n)
        return [n * scale]

    assert cache(load, 2) == [2] and cache(load, 2) == [2]
    assert cache(load, 2, scale=3) == [6]
    assert cache(load, [1]) == [[1]] and cache(load, [1]) == [[1]]  # unhashable: pickled key
    assert calls == [2, 2, [1]]
    cache_clear()
    cache(load, 2)
    assert calls == [2, 2, [1], 2]


def test_panel_decorator_keeps_function():
    from pintu_sdk import panel

    @panel(min_size=(40, 25), max_size=(183, 80))
    def f(w, h):
        return w * h

    assert f(2, 3) == 6
    assert f.__pintu_panel__["min_size"] == (40, 25) and f.__pintu_panel__["max_size"] == (183, 80)

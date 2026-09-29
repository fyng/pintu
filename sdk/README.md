# pintu-sdk

Optional helpers for [pintu](../README.md) recipes. Pure Python, no dependencies.

```python
from pintu_sdk import cache

def timeline(w, h, patient):
    df = cache(load_timeline, patient)   # kept in the recipe kernel's memory across renders
    ...
```

Recipes need not import it; a plain `fn(w, h, **params) -> Figure` works.

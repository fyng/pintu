# pintu-sdk

Optional helpers for [pintu](../README.md) recipes. Pure Python, no dependencies.

```python
from pintu_sdk import cache

def timeline(w, h, patient):
    df = cache(load_timeline, patient)   # kept in the recipe kernel's memory across renders
    ...
```

Recipes need not import it; a plain `fn(w, h, **params) -> Figure` works.

`save` writes a figure and its sidecar `<name>.meta.json`, so pintu's gallery links the
file to the recipe that draws it:

```python
from pintu_sdk import save

save(fig, "scratch/timelines/S001.pdf", recipe="recipes.cohort:timeline", params={"patient": "S001"})
```

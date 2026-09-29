# pintu-sdk

Optional helpers for [pintu](../README.md) recipes. Pure Python, no dependencies.

```python
from pintu_sdk import cache

def timeline(w, h, patient):
    df = cache(load_timeline, patient)   # kept in the recipe kernel's memory across renders
    ...
```

Recipes need not import it; a plain `fn(w, h, **params) -> Figure` works.

`multiples` turns a function that draws one item on one axes into a recipe for a
multiples panel; pintu passes the board's `mosaic` and `share`:

```python
from pintu_sdk import choice, multiples

@multiples(item=choice(list_patients), min_cell=(40, 16))
def timeline(ax, patient): ...

timeline(183, 60, mosaic=[["S001", "S002"], ["S003", "."]], share={"x": "all", "y": "row"})
timeline(89, 40, patient="S001")   # without a mosaic: one item
```

matplotlib is imported only when a figure is drawn.

`save` writes a figure and its sidecar `<name>.meta.json`, so pintu's gallery links the
file to the recipe that draws it:

```python
from pintu_sdk import save

save(fig, "scratch/timelines/S001.pdf", recipe="recipes.cohort:timeline", params={"patient": "S001"})
```

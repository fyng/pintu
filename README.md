# pintu

pintu (拼图, "puzzle") is a storyboard app for academic figures: it lays out panels on a
page grid and assembles the figure with Typst. See [SPEC.md](SPEC.md) for the design and
[docs/dev.md](docs/dev.md) to build and run it.

```sh
cd frontend && npm install && npm run build
cd ../backend && uv run pintu serve --project ../examples/demo
```

MIT licensed.

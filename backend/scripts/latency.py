"""Measures board-preview latency: layout edit posted -> compiled SVG received on the WebSocket.

Usage: uv run python scripts/latency.py <base-url> <board> <panel-id> [n]
"""

import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets


async def main(base: str, board: str, pid: str, n: int = 20) -> None:
    async with httpx.AsyncClient(base_url=base) as http, websockets.connect(base.replace("http", "ws") + "/api/ws", max_size=None) as ws:
        cell = next(p["cell"] for p in (await http.get(f"/api/boards/{board}")).json()["panels"] if p["id"] == pid)
        total, compile_ms = [], []
        for i in range(n):
            c = list(cell)
            c[3] = cell[3] - (i % 2)
            t0 = time.perf_counter()
            r = await http.post(f"/api/boards/{board}/ops", json={"ops": [{"op": "set_cell", "id": pid, "cell": c}]})
            r.raise_for_status()
            rev = r.json()["rev"]
            while True:
                m = json.loads(await ws.recv())
                if m["type"] == "preview" and m["name"] == board and m["rev"] >= rev:
                    break
            total.append((time.perf_counter() - t0) * 1000)
            compile_ms.append(m["ms"])
            await asyncio.sleep(0.2)
        await http.post(f"/api/boards/{board}/ops", json={"ops": [{"op": "set_cell", "id": pid, "cell": cell}]})
    q = lambda xs, f: f"{f(xs):.1f}"
    print(f"n={n} edit->preview ms: median {q(total, statistics.median)} max {q(total, max)}; "
          f"typst codegen+compile ms: median {q(compile_ms, statistics.median)} max {q(compile_ms, max)}")
    print("all:", " ".join(f"{t:.0f}" for t in total))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], *(int(a) for a in sys.argv[4:5])))

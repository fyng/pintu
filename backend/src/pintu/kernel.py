"""Recipe kernel: a warm ipykernel in the project's recipe env (SPEC §4, §8).

``KernelRunner`` starts the kernel on first use, runs ``worker.py`` in it once
as the ``pintu_worker`` module, then calls ``pintu_worker.render`` per render.
Loaded modules and data stay in the kernel's memory between renders.
"""

from __future__ import annotations

import ast
import asyncio
import json
import queue
import re
import time
from typing import Optional

from jupyter_client import AsyncKernelManager
from jupyter_client.kernelspec import KernelSpecManager

from .project import Project
from .recipes import WORKER, RenderRequest, RenderResult, recipe_python, to_result, worker_request

KERNEL_NAME = "pintu-recipe"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LOAD_WORKER = """\
import sys as _sys, types as _types
_m = _types.ModuleType("pintu_worker")
_m.__file__ = {path!r}
exec(compile({src!r}, {path!r}, "exec"), _m.__dict__)
_sys.modules["pintu_worker"] = _m
del _sys, _types, _m
"""
CALL = "import json as _j, pintu_worker as _w\n_pintu_result = _j.dumps(_w.render(_j.loads({payload!r})))"


class KernelError(RuntimeError):
    """The kernel cannot start or run."""


class _Died(Exception):
    pass


class _Output:
    def __init__(self):
        self.stdout: list[str] = []
        self.stderr: list[str] = []
        self.error: Optional[str] = None


class KernelRunner:
    """Renders recipes in one long-lived kernel; renders run one at a time.

    Args:
        project: The project; ``[kernel] python`` in pintu.toml picks the env.
        timeout: Seconds per render before the kernel is interrupted.
        startup_timeout: Seconds to wait for a new kernel.
    """

    def __init__(self, project: Project, timeout: float = 120.0, startup_timeout: float = 60.0):
        self.project = project
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self._km: Optional[AsyncKernelManager] = None
        self._kc = None
        self._lock = asyncio.Lock()
        self._running: Optional[str] = None

    async def _check_ipykernel(self, python: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                python, "-c", "import ipykernel", stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise KernelError(f"cannot run the recipe Python {python}: {e}") from e
        _, err = await proc.communicate()
        if proc.returncode:
            raise KernelError(f"ipykernel is not installed in the recipe env ({python}). Install it with:\n"
                              f"  {python} -m pip install ipykernel\n{err.decode(errors='replace').strip()}")

    def _spec_manager(self, python: str) -> KernelSpecManager:
        d = self.project.root / ".pintu" / "kernels" / KERNEL_NAME
        d.mkdir(parents=True, exist_ok=True)
        spec = {"argv": [python, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                "display_name": "pintu recipes", "language": "python", "env": {"MPLBACKEND": "Agg"}}
        text = json.dumps(spec, indent=1)
        if not (d / "kernel.json").exists() or (d / "kernel.json").read_text() != text:
            (d / "kernel.json").write_text(text)
        return KernelSpecManager(kernel_dirs=[str(d.parent)])

    async def _start(self) -> None:
        python = recipe_python(self.project)
        await self._check_ipykernel(python)
        km = AsyncKernelManager(kernel_name=KERNEL_NAME, kernel_spec_manager=self._spec_manager(python))
        await km.start_kernel(cwd=str(self.project.root))
        kc = km.client()
        kc.start_channels()
        try:
            await kc.wait_for_ready(timeout=self.startup_timeout)
        except RuntimeError as e:
            kc.stop_channels()
            await km.shutdown_kernel(now=True)
            raise KernelError(f"recipe kernel did not start: {e}") from e
        self._km, self._kc = km, kc
        out, _ = await self._execute(LOAD_WORKER.format(path=str(WORKER), src=WORKER.read_text(encoding="utf-8")),
                                     timeout=self.startup_timeout)
        if out.error:
            await self._stop()
            raise KernelError(f"cannot load the pintu worker in the kernel:\n{out.error}")

    async def _stop(self) -> None:
        km, kc = self._km, self._kc
        self._km = self._kc = None
        if kc is not None:
            kc.stop_channels()
        if km is not None:
            try:
                await km.shutdown_kernel(now=True)
            except Exception:
                pass

    async def _execute(self, code: str, timeout: float, expressions: Optional[dict] = None) -> tuple[_Output, dict]:
        """Runs code and collects its output until the kernel is idle.

        Raises:
            asyncio.TimeoutError: The code ran past ``timeout``.
            _Died: The kernel process exited.
        """
        kc, km = self._kc, self._km
        msg_id = kc.execute(code, silent=False, store_history=False, allow_stdin=False,
                            user_expressions=expressions or {})
        self._running = msg_id
        out = _Output()
        await self._drain(msg_id, out, time.monotonic() + timeout)
        while True:
            try:
                reply = await kc.get_shell_msg(timeout=5)
            except queue.Empty:
                if not await km.is_alive():
                    raise _Died()
                continue
            if reply["parent_header"].get("msg_id") == msg_id:
                return out, reply["content"]

    async def _drain(self, msg_id: str, out: _Output, deadline: float) -> None:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise asyncio.TimeoutError()
            try:
                msg = await self._kc.get_iopub_msg(timeout=min(left, 0.5))
            except queue.Empty:
                if not await self._km.is_alive():
                    raise _Died()
                continue
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            kind, c = msg["msg_type"], msg["content"]
            if kind == "stream":
                (out.stdout if c.get("name") == "stdout" else out.stderr).append(c.get("text", ""))
            elif kind == "error":
                out.error = ANSI.sub("", "\n".join(c.get("traceback") or [f"{c.get('ename')}: {c.get('evalue')}"]))
            elif kind == "status" and c.get("execution_state") == "idle":
                return

    async def _interrupt(self) -> bool:
        """Interrupts the running code. Returns whether the kernel went idle."""
        try:
            await self._km.interrupt_kernel()
            while True:
                msg = await self._kc.get_iopub_msg(timeout=10)
                if (msg["msg_type"] == "status" and msg["content"].get("execution_state") == "idle"
                        and msg["parent_header"].get("msg_id") == self._running):
                    return True
        except Exception:
            return False

    async def render(self, req: RenderRequest) -> RenderResult:
        """Renders one recipe call; never raises for recipe or kernel errors."""
        async with self._lock:
            t0 = time.perf_counter()
            try:
                if self._km is None or not await self._km.is_alive():
                    await self._stop()
                    await self._start()
            except KernelError as e:
                return RenderResult(ok=False, error=str(e), seconds=time.perf_counter() - t0)
            code = CALL.format(payload=json.dumps(worker_request(self.project, req)))
            try:
                out, reply = await self._execute(code, self.timeout, {"r": "_pintu_result"})
            except asyncio.TimeoutError:
                if not await self._interrupt():
                    await self._stop()
                return RenderResult(ok=False, error=f"render timed out after {self.timeout:g} s; the kernel was interrupted",
                                    seconds=time.perf_counter() - t0)
            except _Died:
                await self._stop()
                return RenderResult(ok=False, error="the recipe kernel died during the render; it restarts on the next render",
                                    seconds=time.perf_counter() - t0)
            stdout, stderr = "".join(out.stdout), "".join(out.stderr)
            expr = (reply.get("user_expressions") or {}).get("r") or {}
            if out.error or expr.get("status") != "ok":
                return RenderResult(ok=False, error=out.error or "no result from the kernel", stdout=stdout,
                                    stderr=stderr, seconds=time.perf_counter() - t0)
            raw = json.loads(ast.literal_eval(expr["data"]["text/plain"]))
            return to_result(raw, req, stdout=stdout, stderr=stderr)

    async def restart(self) -> None:
        """Stops the kernel; the next render starts a fresh one without loaded data."""
        async with self._lock:
            await self._stop()

    async def close(self) -> None:
        """Shuts the kernel down."""
        async with self._lock:
            await self._stop()

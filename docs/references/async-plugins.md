<!--
  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
  SPDX-License-Identifier: Apache-2.0
-->
# Writing plugins: async first, sync still works

The runtime is async: it awaits the driver, engines, call layers, LLM providers, tools, sends, delegation and
the session turn on one event loop. **Write new plugins as `async def`.** A plugin written sync keeps working. The
runtime runs its method on a worker thread so it cannot block the loop, and logs one warning per plugin class
that says which async method to write.

If you have written plugins before, nothing you shipped stops working. This page tells you what changes if you
move them to async, and what the runtime does while you have not.

## What you write

| Plugin kind | Write (async) | Still accepted (sync) | Where a sync one runs |
| --- | --- | --- | --- |
| Tool | `async def on_execute_tool` (or `async def execute`) | `def on_execute_tool` | A worker thread; `engine.tool_worker` / `engine.tool_concurrency` |
| LLM provider | `async def achat_completion` | `def chat_completion` | The shared worker threads |
| Agent comm link | `async def asend` | `def send` | The shared worker threads |
| Layer | `async def asend(envelope)` | `def send(envelope)` | The shared worker threads |
| Engine (the bottom of a call chain) | `async def ainvoke` | `def invoke` | The shared worker threads |
| Call layer (wraps the call below) | `async def ainvoke` | none: a layer awaits the call below, so write it async | — |
| System plugin | `async def acall` | `def call` | Inline on the event loop (it may read session state), so keep it short |
| Engine tool, delegation | `async def acall`, `acall_delegate_tool` | `def call`, `call_delegate_tool` | The shared worker threads |
| Hook (summarizer, context, session turn) | `async def` | `def` | Awaited when it returns an awaitable |

"The shared worker threads" are `engine.sync_threads` in the [RuntimeEngine manifest](../manifests/runtime-engine.md).
The default is `1`: sync plugins run one at a time. Raise it when sync plugins must overlap. Async plugins never use
these threads.

## Moving a sync plugin to async

1. **Rename and `await`.** `def chat_completion(...)` becomes `async def achat_completion(...)`; `requests` or
   `httpx.Client` calls become `await` calls on an `httpx.AsyncClient`.
2. **Keep the client, not the call.** An async client belongs to the event loop it first ran on. Create it lazily
   on first use and reuse it across calls to keep connections open. `mas.runtime.aio.LoopCache` keeps one per loop and
   closes them from a sync `stop()`.
3. **Call the layer below with `await`.** A sync layer writes `self._require().send(envelope)`. An async layer
   writes `await self._require().asend(envelope)`.
4. **Do not block.** `time.sleep`, a blocking socket read or a CPU loop inside an `async def` holds the whole loop.
   Use `await asyncio.sleep`, an async client, or `await mas.runtime.aio.run_sync(fn, ...)` for the one blocking call.

## Things that stay the same

- A manifest does not change.
- A tool sees the same frozen context wherever it runs.
- A sync layer can still send down with `self._require().send(...)`; the runtime hands the call back to the loop.
- A sync caller (a script, a test) uses `SessionController.run_turn`, `feed` or `submit_hitl`. Each runs the async
  implementation on its own loop and raises if called inside a running loop; inside one, `await` the async method.

## Keep in mind

- Sync plugins share `engine.sync_threads` threads, so with the default one blocked plugin delays the others. A sync plugin that hangs holds its thread until it returns: a timeout around it cannot stop the thread. A sync
  layer that waits on another sync layer does not deadlock: the inner call gets a thread of its own.
- Async does not mean single-threaded: sync tools and plugins already use threads, and you can raise their count.

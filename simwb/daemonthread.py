"""asyncio.to_thread that does not keep the process alive.

asyncio.to_thread (and loop.run_in_executor) run the call in a ThreadPoolExecutor, whose worker threads Python joins when the
interpreter shuts down: a tool call of the assistant that waits for a job ("wait for the run", up to 10 minutes) kept the closed
workbench running, invisible, until the wait ended - while the job itself went on working and using memory. A call run here is on a
daemon thread: it is dropped when the process ends, and closing the window ends the process."""
from __future__ import annotations

import asyncio
import threading


async def to_thread(fn, *args, **kw):
    """Run fn(*args, **kw) on a daemon thread and await its result (as asyncio.to_thread does)."""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def deliver(setter, value):
        if not fut.done():
            setter(value)

    def run():
        try:
            res = fn(*args, **kw)
        except BaseException as e:  # noqa: BLE001 - handed to the awaiting coroutine
            err, res = e, None
        else:
            err = None
        try:
            if err is not None:
                loop.call_soon_threadsafe(deliver, fut.set_exception, err)
            else:
                loop.call_soon_threadsafe(deliver, fut.set_result, res)
        except RuntimeError:  # (the loop is closed: nobody is waiting any more)
            pass

    threading.Thread(target=run, daemon=True, name="simwb-call").start()
    return await fut

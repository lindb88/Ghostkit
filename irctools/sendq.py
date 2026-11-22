"""irctools.sendq: send to an IRC server without being kicked off for flooding.

Servers disconnect clients that send too much too fast ("Excess Flood"). SendQueue lets a few lines through
at once (burst), then one line every interval seconds; urgent lines (PONG, a kick) go before the rest.

    q = SendQueue(writer, burst=4, interval=1.5)
    task = asyncio.ensure_future(q.run())
    q.put('PRIVMSG #test :hello')
    q.put('PONG :x', urgent=True)
"""

import asyncio
import time
from collections import deque


class SendQueue:
    def __init__(self, writer, burst=4, interval=1.5, on_send=None):
        self.writer, self.burst, self.interval = writer, burst, interval
        self.on_send = on_send                         # on_send(line): for logging
        self.hi, self.lo = deque(), deque()
        self.event = asyncio.Event()
        self.closed = False

    def put(self, line, urgent=False):
        (self.hi if urgent else self.lo).append(line)
        self.event.set()

    def __len__(self):
        return len(self.hi) + len(self.lo)

    def clear(self):
        self.hi.clear()
        self.lo.clear()

    async def run(self):
        """Write the lines at the allowed pace, until close()."""
        tokens = float(self.burst)
        last = time.monotonic()
        while not self.closed:
            if not self.hi and not self.lo:
                self.event.clear()
                await self.event.wait()
                continue
            interval = max(0.01, self.interval)
            now = time.monotonic()
            tokens = min(float(self.burst), tokens + (now - last) / interval)
            last = now
            if tokens < 1:
                await asyncio.sleep((1 - tokens) * interval)
                continue
            line = self.hi.popleft() if self.hi else self.lo.popleft()
            tokens -= 1
            if self.on_send:
                self.on_send(line)
            self.writer.write((line + '\r\n').encode('utf-8', 'replace'))
            await self.writer.drain()

    def close(self):
        self.closed = True
        self.event.set()

"""examples/botnet/link: the link protocol (docs/botnet.md), its port under attack (docs/security.md point 5),
shared data, and the linked bots tested with irclab."""
import asyncio
import json
import os
import shutil
import ssl
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK = os.path.join(ROOT, 'examples', 'botnet', 'link')
sys.path.insert(0, LINK)
import botlink as bl  # noqa: E402
import logging  # noqa: E402

bl.log.addHandler(logging.NullHandler())          # the refusals these tests cause are expected
bl.log.propagate = False

SECRET = 'correct-horse-battery-staple'


class Protocol(unittest.TestCase):
    def test_the_guides_example(self):
        """The fixed example in docs/botnet.md, so another language's implementation can check against it."""
        self.assertEqual(bl.link_mac(SECRET, 'leaf', '8c1f4e2a9b3d7f60', 'leaf1'),
                         '464e8d3c350972b43cfee1b4b8cad5741b1157c3929dd3421e2be34040bf2e73')
        self.assertEqual(bl.link_mac(SECRET, 'hub', '3e9a0d5c71b24f86', 'leaf1'),
                         '0e025a4aee37806b7c3d134940c25204226507bd74337e631b6110d0db608bb2')
        with open(os.path.join(ROOT, 'docs', 'botnet.md')) as f:
            doc = f.read()
        self.assertIn('464e8d3c350972b43cfee1b4b8cad5741b1157c3929dd3421e2be34040bf2e73', doc)

    def test_messages(self):
        self.assertEqual(bl.decode(b'{"t": "ping"}\n'), {'t': 'ping'})
        d, depth = bl.decode(b'{"d": ' + b'[' * 50 + b']' * 50 + b'}')['d'], 0
        while isinstance(d, list):
            d, depth = (d[0] if d else None), depth + 1
        self.assertEqual(depth, 50)                       # 50 deep is fine
        for bad in (b'[]', b'null', b'"x"', b'7', b'{"ts": NaN}', b'{"ts": Infinity}', b'\xff{}', b'{"a":',
                    b'{"d": ' + b'[' * 150 + b']' * 150 + b'}', b'[' * 100000 + b']' * 100000):
            with self.assertRaises(ValueError, msg=bad[:30]):
                bl.decode(bad)
        with self.assertRaises(ValueError):
            bl.encode({'ts': float('nan')})               # and we never send one either

    def test_chunks_fit_a_line(self):
        entries = {'k%d' % i: {'v': 'x' * 200, 'ts': 1.0} for i in range(2000)}
        parts = list(bl.chunks(entries))
        self.assertGreater(len(parts), 1)
        self.assertEqual(sum(len(p) for p in parts), 2000)
        self.assertTrue(all(len(bl.encode({'t': 'put', 'entries': p})) < bl.LINE_MAX for p in parts))


class SharedData(unittest.TestCase):
    def test_newest_wins_and_deletions_are_remembered(self):
        a, b = bl.Shared(), bl.Shared()
        b.merge(a.set('tea', 'Earl Grey'))
        self.assertEqual(b.get('tea'), 'Earl Grey')
        time.sleep(0.01)
        change = b.delete('tea')
        a.merge(change)
        self.assertIsNone(a.get('tea'))
        self.assertIn('tea', a.entries)                   # a marker, so an old copy cannot bring it back
        self.assertEqual(a.merge({'tea': {'v': 'Earl Grey', 'ts': change['tea']['ts'] - 5}}), {})
        self.assertIsNone(a.get('tea'))

    def test_two_hubs_end_the_same_in_any_order(self):
        import random
        now = time.time()
        ea = {'bob': {'v': 'op', 'ts': now}, 'ann': {'v': 'master', 'ts': now - 1}, 'eve': {'v': None, 'ts': now - 5}}
        eb = {'bob': {'v': 'voice', 'ts': now}, 'ann': {'v': 'friend', 'ts': now - 2}, 'eve': {'v': 'op', 'ts': now - 6}}
        a, b = bl.Shared(), bl.Shared()
        a.merge(ea)
        b.merge(eb)
        msgs = [(a, {k: v}) for k, v in eb.items()] + [(b, {k: v}) for k, v in ea.items()]
        rnd = random.Random(3)
        for _ in range(3):
            rnd.shuffle(msgs)
            for to, e in msgs:
                to.merge(json.loads(json.dumps(e)))
        self.assertEqual(a.entries, b.entries)
        self.assertEqual(a.items(), {'bob': 'voice', 'ann': 'master'})   # a tie settled the same on both

    def test_clock_trouble_and_garbage_change_nothing(self):
        a = bl.Shared()
        a.set('tea', 'Earl Grey')
        before = json.dumps(a.entries, sort_keys=True)
        a.merge({'tea': {'v': 'x', 'ts': time.time() + 86400}, 'b': {'v': 1, 'ts': True}, 'c': {'v': 1, 'ts': '1'},
                 'd': 'not an entry', 'e': {'v': 1, 'ts': -5}, 'f': {'v': 1}})
        self.assertEqual(json.dumps(a.entries, sort_keys=True), before)
        with self.assertRaises(ValueError):
            a.merge([1, 2])

    def test_kept_in_a_file(self):
        d = tempfile.mkdtemp()
        try:
            fn = os.path.join(d, 'notes.json')
            bl.Shared(fn).set('tea', 'Earl Grey')
            self.assertEqual(bl.Shared(fn).get('tea'), 'Earl Grey')
        finally:
            shutil.rmtree(d)


@unittest.skipUnless(shutil.which('openssl'), 'needs openssl to make a certificate')
class LinkPort(unittest.TestCase):
    """The hub's port with a real leaf, and with what a stranger might send."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.crt, cls.key = os.path.join(cls.dir, 'link.crt'), os.path.join(cls.dir, 'link.key')
        bl.make_cert(cls.crt, cls.key, 'Hub')
        cls.fp = bl.cert_fingerprint(cls.crt)
        cls.other = os.path.join(cls.dir, 'other.crt')
        bl.make_cert(cls.other, os.path.join(cls.dir, 'other.key'), 'Not the hub')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def run_hub(self, test, **kw):
        async def go():
            seen = []
            hub = bl.Hub('Hub', SECRET, self.crt, self.key, host='127.0.0.1', port=0,
                         on_message=lambda n, m: seen.append((n, m)), **kw)
            await hub.start()
            port = hub.server.sockets[0].getsockname()[1]
            try:
                await asyncio.wait_for(test(hub, port, seen), 60)
            finally:
                hub.close()
        asyncio.run(go())

    def leaf(self, port, name='Leaf1', secret=SECRET, fp=None, **kw):
        lf = bl.Leaf(name, secret, '127.0.0.1:%d' % port, fp or self.fp, **kw)
        lf.retry_first = lf.retry_max = 0.2
        return lf

    async def until(self, cond, t=10):
        end = time.time() + t
        while not cond():
            if time.time() > end:
                raise AssertionError('timed out')
            await asyncio.sleep(0.05)

    async def raw(self, port):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        return await asyncio.open_connection('127.0.0.1', port, ssl=ctx, limit=1 << 22)

    async def answer(self, r, t=5):
        try:
            line = await asyncio.wait_for(r.readline(), t)
        except (ConnectionError, ssl.SSLError):
            return None
        return json.loads(line) if line else None

    def test_a_leaf_links_and_data_goes_both_ways(self):
        async def t(hub, port, seen):
            hub.shared.set('from_hub', 1)
            lf = self.leaf(port)
            lf.shared.set('from_leaf', 2)
            task = asyncio.ensure_future(lf.run())
            await self.until(lambda: lf.linked and 'Leaf1' in hub.leaves)
            await self.until(lambda: lf.shared.get('from_hub') == 1 and hub.shared.get('from_leaf') == 2)
            lf.put(lf.shared.set('later', 3))
            await self.until(lambda: hub.shared.get('later') == 3)
            hub.put(hub.shared.set('news', 4))
            await self.until(lambda: lf.shared.get('news') == 4)
            lf.send({'t': 'status', 'name': 'SomeoneElse', 'chans': ['#a']})
            await self.until(lambda: any(m.get('t') == 'status' for _, m in seen))
            self.assertEqual([n for n, m in seen if m.get('t') == 'status'], ['Leaf1'])   # by its login name
            lf.close()
            task.cancel()
        self.run_hub(t)

    def test_wrong_secret_or_certificate(self):
        async def t(hub, port, seen):
            start = time.time()
            self.assertFalse(await self.leaf(port, secret='a-wrong-secret-here').link_once())
            self.assertGreaterEqual(time.time() - start, 0.9)          # a second before the no
            self.assertFalse(await self.leaf(port, fp=bl.cert_fingerprint(self.other)).link_once())
            self.assertFalse(await self.leaf(port, name='Hub').link_once())   # not under the hub's own name
            self.assertEqual(hub.leaves, {})
        self.run_hub(t)

    def test_a_recorded_login_is_useless(self):
        async def t(hub, port, seen):
            r, w = await self.raw(port)
            w.write(bl.encode({'t': 'hello', 'name': 'Leaf1', 'nonce': 'n' * 32, 'role': 'leaf'}))
            ch = await self.answer(r)
            recorded = {'t': 'auth', 'mac': bl.link_mac(SECRET, 'leaf', ch['nonce'], 'Leaf1')}
            w.write(bl.encode(recorded))
            self.assertEqual((await self.answer(r))['t'], 'ok')
            w.close()
            r, w = await self.raw(port)
            w.write(bl.encode({'t': 'hello', 'name': 'Leaf1', 'nonce': 'n' * 32, 'role': 'leaf'}))
            await self.answer(r)                             # a new challenge
            w.write(bl.encode(recorded))
            self.assertEqual(await self.answer(r), {'t': 'denied'})
            w.close()
        self.run_hub(t)

    def test_nonsense_before_the_login(self):
        async def t(hub, port, seen):
            for line in (b'[]\n', b'null\n', b'not json\n', b'{"t":"hello","name":["Leaf1"],"nonce":"nnnnnnnnnnnnnnnn"}\n',
                         b'{"t":"hello","name":"Leaf1","nonce":"short"}\n', b'{"t":"hello","name":"x y","nonce":"%s"}\n'
                         % (b'n' * 20), b'{"x":"' + b'a' * 100000 + b'"}\n', b'{"t":"hello","name":"L","nonce":NaN}\n'):
                r, w = await self.raw(port)
                w.write(line)
                self.assertIsNone(await self.answer(r), line[:40])     # closed, nothing said
                w.close()
            self.assertEqual(hub.pending, {})                          # every waiting login was let go
        self.run_hub(t)

    def test_idle_connections(self):
        async def t(hub, port, seen):
            idle = [await self.raw(port) for _ in range(bl.PENDING_PER_IP)]
            await self.until(lambda: hub.pending.get('127.0.0.1') == bl.PENDING_PER_IP)
            r, w = await self.raw(port)
            self.assertIsNone(await self.answer(r, 3))                # the fifth: closed at once
            w.close()
            for _, wi in idle:
                wi.close()
            await self.until(lambda: not hub.pending)
            lf = self.leaf(port)
            self.assertTrue(await asyncio.wait_for(self._links(lf, hub), 10))
        self.run_hub(t)

    async def _links(self, lf, hub):
        task = asyncio.ensure_future(lf.run())
        await self.until(lambda: 'Leaf1' in hub.leaves)
        lf.close()
        task.cancel()
        return True

    def test_after_the_login(self):
        """Wrong types are skipped and the link stays; NaN or deep nesting ends it; it can link again."""
        async def t(hub, port, seen):
            lf = self.leaf(port)
            task = asyncio.ensure_future(lf.run())
            await self.until(lambda: lf.linked)
            for m in ({'t': 'put', 'entries': [1]}, {'t': 'put', 'entries': 'all'},
                      {'t': 'put', 'entries': {'k': {'v': 1, 'ts': time.time() + 99999}}}):
                lf.send(m)
            lf.send({'t': 'ping'})
            await asyncio.sleep(0.5)
            self.assertTrue(lf.linked)
            self.assertNotIn('k', hub.shared.entries)
            first = hub.leaves['Leaf1']
            lf.hub.writer.write(b'{"t":"put","entries":{"x":{"v":1,"ts":NaN}}}\n')
            await self.until(lambda: hub.leaves.get('Leaf1') is not first)    # ended; then linked again
            await self.until(lambda: 'Leaf1' in hub.leaves)
            lf.close()
            task.cancel()
        self.run_hub(t)

    def test_a_second_login_replaces_the_first_and_keeps_working(self):
        """The replacement trap: the old link's cleanup must not remove the new one."""
        async def t(hub, port, seen):
            a = self.leaf(port)
            ta = asyncio.ensure_future(a.run())
            await self.until(lambda: a.linked)
            b = self.leaf(port)
            self.assertFalse(b.linked)
            tb = asyncio.ensure_future(b.link_once())
            await self.until(lambda: b.linked)
            a.close()
            ta.cancel()
            await asyncio.sleep(0.5)
            self.assertIn('Leaf1', hub.leaves)                         # still linked: the new one
            self.assertTrue(hub.send('Leaf1', {'t': 'hi'}))
            got = []
            b.on_message = got.append
            hub.send('Leaf1', {'t': 'hi'})
            await self.until(lambda: got)
            b.close()
            tb.cancel()
        self.run_hub(t)

    def test_too_much_ends_a_link(self):
        """A logged-in bot that sends more than RATE_MSGS messages in a minute is cut off (a stolen bot)."""
        async def t(hub, port, seen):
            old = bl.RATE_MSGS
            bl.RATE_MSGS = 50
            try:
                r, w = await self.raw(port)
                w.write(bl.encode({'t': 'hello', 'name': 'Noisy', 'nonce': 'n' * 32, 'role': 'leaf'}))
                ch = await self.answer(r)
                w.write(bl.encode({'t': 'auth', 'mac': bl.link_mac(SECRET, 'leaf', ch['nonce'], 'Noisy')}))
                await self.until(lambda: 'Noisy' in hub.leaves)
                for i in range(80):
                    w.write(bl.encode({'t': 'status', 'n': i}))
                await self.until(lambda: 'Noisy' not in hub.leaves, 5)
                self.assertLessEqual(len([m for n, m in seen if m.get('t') == 'status']), 50)
                w.close()
            finally:
                bl.RATE_MSGS = old
        self.run_hub(t)

    def test_silence_ends_a_link(self):
        async def t(hub, port, seen):
            old = bl.PING_EVERY, bl.DEAD_AFTER
            bl.PING_EVERY, bl.DEAD_AFTER = 0.2, 0.6
            try:
                r, w = await self.raw(port)
                w.write(bl.encode({'t': 'hello', 'name': 'Quiet', 'nonce': 'n' * 32, 'role': 'leaf'}))
                ch = await self.answer(r)
                w.write(bl.encode({'t': 'auth', 'mac': bl.link_mac(SECRET, 'leaf', ch['nonce'], 'Quiet')}))
                await self.until(lambda: 'Quiet' in hub.leaves)
                await self.until(lambda: 'Quiet' not in hub.leaves, 5)  # never answered a ping: dropped
                w.close()
            finally:
                bl.PING_EVERY, bl.DEAD_AFTER = old
        self.run_hub(t)


@unittest.skipUnless(shutil.which('openssl'), 'needs openssl to make a certificate')
class LinkedBots(unittest.TestCase):
    def run_file(self, name):
        from irclab.scenario import Runner
        with open(os.path.join(LINK, name)) as f:
            out = []
            ok = asyncio.run(Runner(f.read(), out=out.append, base=LINK).run())
        self.assertTrue(ok, '\n'.join(out))

    def test_alone(self):
        self.run_file('linkbot-alone.scenario')

    def test_hub_and_leaf(self):
        self.run_file('linkbot.scenario')


if __name__ == '__main__':
    unittest.main()

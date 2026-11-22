"""irctools: the line format, case rules, masks, pacing, and the Bot against the irclab server."""

import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from irclab import IRCServer, User, until                      # noqa: E402
from irctools import (Bot, CaseDict, SendQueue, ban_mask, build, equal, is_channel, lower, match,  # noqa: E402
                      parse, split_text)


class TestMessage(unittest.TestCase):
    def test_parse(self):
        m = parse('@time=2026-01-01T00:00:00Z;account=bob :Bob!~bob@h.example PRIVMSG #chan :hello there')
        self.assertEqual(m.command, 'PRIVMSG')
        self.assertEqual((m.nick, m.user, m.host), ('Bob', '~bob', 'h.example'))
        self.assertEqual(m.target, '#chan')
        self.assertEqual(m.text, 'hello there')
        self.assertEqual(m.tags['account'], 'bob')

    def test_parse_tag_escapes(self):
        m = parse(r'@a=x\sy\:z\\w :s NOTICE * :x')
        self.assertEqual(m.tags['a'], 'x y;z\\w')

    def test_parse_no_prefix(self):
        m = parse('PING :abc')
        self.assertEqual((m.command, m.text, m.nick), ('PING', 'abc', ''))

    def test_build(self):
        self.assertEqual(build('PRIVMSG', '#c', 'hi'), 'PRIVMSG #c hi')
        self.assertEqual(build('PRIVMSG', '#c', 'hi there'), 'PRIVMSG #c :hi there')
        self.assertEqual(build('PRIVMSG', '#c', ':)'), 'PRIVMSG #c ::)')
        self.assertEqual(build('PRIVMSG', '#c', ''), 'PRIVMSG #c :')

    def test_build_cannot_inject(self):
        line = build('PRIVMSG', '#c', 'hi\r\nQUIT :owned\x00')
        self.assertNotIn('\r', line)
        self.assertNotIn('\n', line)
        self.assertNotIn('\0', line)

    def test_split_text(self):
        parts = split_text('word ' * 200, 100)
        self.assertTrue(all(len(p.encode()) <= 100 for p in parts))
        self.assertEqual(' '.join(parts).split(), ['word'] * 200)
        # utf-8 characters are never cut in half
        parts = split_text('å' * 300, 101)
        self.assertTrue(all(len(p.encode()) <= 101 for p in parts))
        self.assertEqual(''.join(parts), 'å' * 300)


class TestCaseAndMasks(unittest.TestCase):
    def test_lower(self):
        self.assertEqual(lower('Bob[away]~'), 'bob{away}^')
        self.assertEqual(lower('Bob[away]~', 'ascii'), 'bob[away]~')
        self.assertTrue(equal('Bob[x]', 'BOB{X}'))

    def test_casedict(self):
        d = CaseDict()
        d['Bob[x]'] = 1
        self.assertIn('bob{x}', d)
        self.assertEqual(d.get('BOB{X}'), 1)
        self.assertEqual(d.pop('bob{X}'), 1)
        self.assertEqual(len(d), 0)

    def test_match(self):
        self.assertTrue(match('*!*bob@*.example.com', 'Bob!~bob@home.example.com'))
        self.assertTrue(match('b?b!*@*', 'BOB!x@y'))
        self.assertFalse(match('*!*bob@*.example.com', 'Bob!~bob@example.org'))
        self.assertFalse(match('a.b!*@*', 'axb!x@y'))                  # . is not a wildcard

    def test_ban_mask(self):
        self.assertEqual(ban_mask('Bob', '~bob', 'h'), '*!*bob@h')
        self.assertEqual(ban_mask('Bob', '~bob', 'h', 'host'), '*!*@h')
        self.assertEqual(ban_mask('Bob', 'bob', 'h', 'nick'), 'Bob!*@*')

    def test_is_channel(self):
        self.assertTrue(is_channel('#a'))
        self.assertFalse(is_channel('Bob'))
        self.assertTrue(is_channel('!x', '#!'))


class FakeWriter:
    def __init__(self):
        self.lines, self.times = [], []

    def write(self, data):
        self.lines.append(data.decode().rstrip('\r\n'))
        self.times.append(time.monotonic())

    async def drain(self):
        pass


class TestSendQueue(unittest.TestCase):
    def test_pacing_and_urgent(self):
        async def go():
            w = FakeWriter()
            q = SendQueue(w, burst=2, interval=0.2)
            t = asyncio.ensure_future(q.run())
            for i in range(4):
                q.put('PRIVMSG #c :%d' % i)
            q.put('PONG :x', urgent=True)
            await asyncio.sleep(0.75)
            q.close()
            await t
            return w
        w = asyncio.run(go())
        self.assertEqual(w.lines[0], 'PONG :x')                          # urgent first
        self.assertEqual(len(w.lines), 5)
        # two at once, then one per 0.2 s
        self.assertLess(w.times[1] - w.times[0], 0.05)
        self.assertGreater(w.times[4] - w.times[0], 0.5)


def make_bot(port, **kw):
    kw.setdefault('channels', ['#test'])
    return Bot(kw.pop('nick', 'TestBot'), '127.0.0.1', port, interval=0.02, burst=50, **kw)


class TestBot(unittest.TestCase):
    """The Bot against a real (local) IRC server."""

    def run_async(self, coro):
        return asyncio.run(asyncio.wait_for(coro, 30))

    def test_join_commands_and_names(self):
        async def go():
            s = await IRCServer('plain').start()
            bot = make_bot(s.port)

            @bot.command('!hi')
            async def hi(ctx):
                ctx.reply('hi %s (%s)' % (ctx.nick, ctx.where))

            seen = []

            @bot.on('JOIN')
            async def on_join(b, msg):
                seen.append(msg.nick)

            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 10)
                alice = User('Alice')
                await alice.connect(s.port)
                await alice.join('#test')
                await until(lambda: 'alice' in bot.channels['#test'].users, 5)
                self.assertTrue(bot.is_op('#test'))                    # first in: the server gave ops
                alice.say('#test', '!hi')
                line = await alice.expect(lambda l: l.command == 'PRIVMSG' and l.nick == 'TestBot', 5)
                self.assertEqual(line.text, 'hi Alice (#test)')
                alice.say('TestBot', 'hi')                               # private: no ! needed
                line = await alice.expect(lambda l: l.command == 'PRIVMSG' and l.target == 'Alice', 5)
                self.assertEqual(line.text, 'hi Alice (None)')
                self.assertIn('Alice', seen)
                alice.send('NICK Alicia')
                await until(lambda: 'alicia' in bot.channels['#test'].users, 5)
                self.assertNotIn('alice', bot.channels['#test'].users)
                alice.part('#test')
                await until(lambda: 'alicia' not in bot.channels['#test'].users, 5)
                alice.close()
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_reply_cannot_inject_lines(self):
        async def go():
            s = await IRCServer('plain').start()
            bot = make_bot(s.port)

            @bot.command('!echo')
            async def echo(ctx):
                ctx.reply('you said ' + ctx.args + '\r\nQUIT :bye')

            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 10)
                bob = User('Bob')
                await bob.connect(s.port)
                await bob.join('#test')
                bob.say('#test', '!echo x')
                line = await bob.expect(lambda l: l.command == 'PRIVMSG' and l.nick == 'TestBot', 5)
                self.assertIn('QUIT', line.text)                       # QUIT stayed text in the message
                await asyncio.sleep(0.3)
                self.assertIsNotNone(s.client('TestBot'))              # and the bot is still connected
                bob.close()
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_nick_taken_and_sasl(self):
        async def go():
            s = await IRCServer('libera').start()
            s.add_account('botacct', 'secret')
            squatter = User('TestBot')
            await squatter.connect(s.port)
            bot = make_bot(s.port, sasl=('botacct', 'secret'))
            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: bot.connected, 10)
                self.assertEqual(bot.nick, 'TestBot_')
                self.assertIn('sasl', bot.caps)
                await until(lambda: s.client('TestBot_') is not None and s.client('TestBot_').account == 'botacct', 5)
                squatter.close()
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_reconnects_and_rejoins(self):
        async def go():
            s = await IRCServer('plain').start()
            bot = make_bot(s.port)
            import irctools.client as C
            task = asyncio.ensure_future(bot.run_forever())
            orig = asyncio.sleep

            async def quick(t, *a):                                    # the reconnect wait, shortened
                await orig(min(t, 0.2), *a)
            C.asyncio.sleep = quick
            try:
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 10)
                s.kill('TestBot', 'test')
                await until(lambda: not bot.connected, 5)
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 10)
            finally:
                C.asyncio.sleep = orig
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())


if __name__ == '__main__':
    unittest.main()

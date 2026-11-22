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


Q_VECTOR = '5a22a183e2189e27b0c78a69b5315d04a0492796ad29c3f99dee288eb33b8781'


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

    def test_quakenet_q_login_hides_the_host_before_joining(self):
        async def go():
            s = await IRCServer('quakenet').start()
            s.add_account('BotAcct', 'secretpassword')
            joined_host = {}
            orig = s.on_line

            def watch(nick, line):
                if line.upper().startswith('JOIN') and nick == 'TestBot':
                    joined_host.setdefault('host', s.client('TestBot').host)
                if orig:
                    orig(nick, line)
            s.on_line = watch
            bot = make_bot(s.port, q_auth=('BotAcct', 'secretpassword'))
            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 15)
                self.assertEqual(bot.account, 'BotAcct')
                self.assertEqual(joined_host['host'], 'BotAcct.users.quakenet.org')   # hidden before the join
                self.assertFalse(any('secretpassword' in line for _, line in s.received))  # never sent
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_quakenet_wrong_password(self):
        async def go():
            s = await IRCServer('quakenet').start()
            s.add_account('BotAcct', 'secretpassword')
            # q_required: stays out of channels
            strict = make_bot(s.port, nick='Strict', q_auth=('BotAcct', 'wrong'), q_required=True)
            # default: joins anyway, unhidden
            loose = make_bot(s.port, nick='Loose', q_auth=('BotAcct', 'wrong'))
            tasks = [asyncio.ensure_future(b.run_forever()) for b in (strict, loose)]
            try:
                await until(lambda: s.chan('#test') and 'loose' in s.chan('#test').members, 15)
                await asyncio.sleep(1)
                self.assertNotIn('strict', s.chan('#test').members)
                self.assertEqual((strict.account, loose.account), ('', ''))
            finally:
                for b in (strict, loose):
                    b.stop()
                for t in tasks:
                    t.cancel()
                await s.stop()
        self.run_async(go())

    def test_a_fake_q_is_ignored(self):
        async def go():
            s = await IRCServer('plain').start()
            bot = make_bot(s.port, q_auth=('BotAcct', 'pw'), hide_host=False)
            import irctools.client as C
            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: bot.connected and bot._q_state == 'challenge', 10)
                faker = User('Q')                        # a user called Q, not the real one (CServe)
                await faker.connect(s.port)
                faker.notice('TestBot', 'You are now logged in as Someone.')
                faker.notice('TestBot', 'CHALLENGE abc HMAC-SHA-256')
                await asyncio.sleep(1)
                self.assertEqual((bot.account, bot._q_state), ('', 'challenge'))
                self.assertFalse(s.chan('#test'))
                faker.close()
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_q_response_matches_quakenet(self):
        from irclab import q_expected
        from irctools.client import q_response
        self.assertEqual(q_response('Bot[1]', 'a long password', 'abc123'), q_expected('Bot[1]', 'a long password', 'abc123'))
        self.assertEqual(q_response('bot{1}', 'a long passXXX', 'abc123'), q_expected('Bot[1]', 'a long password', 'abc123'))
        from irctools import q_password_hash
        hashed = 'sha256:' + q_password_hash('a long password')
        self.assertEqual(q_response('Bot[1]', hashed, 'abc123'), q_expected('Bot[1]', 'a long password', 'abc123'))
        # the fixed example in docs/quakenet.md, for checking an implementation in any language
        self.assertEqual(q_response('MyAccount', 'correcthorse', 'f2a4c8b0e1d3a5c7f9e2b4d6a8c0e1f3'), Q_VECTOR)

    def cap_server(self, ack_lines, ls_lines=(':srv CAP * LS :sasl account-tag multi-prefix',)):
        """A scripted server: its LS and ACK answers as given, then SASL and the welcome. Returns (server, got)."""
        got = []

        async def handle(reader, writer):
            def send(line):
                writer.write((line + '\r\n').encode())
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().strip()
                got.append(line)
                if line.startswith('CAP LS'):
                    for l in ls_lines:
                        send(l)
                elif line.startswith('CAP REQ'):
                    for l in ack_lines:
                        send(l)
                elif line == 'AUTHENTICATE PLAIN':
                    send('AUTHENTICATE +')
                elif line.startswith('AUTHENTICATE '):
                    send(':srv 900 TestBot TestBot!t@h acct :You are now logged in as acct')
                    send(':srv 903 TestBot :SASL authentication successful')
                elif line == 'CAP END':
                    send(':srv 001 TestBot :Welcome')
                    send(':srv 376 TestBot :End of MOTD')
                await writer.drain()
        return handle, got

    def run_cap(self, ack_lines, **kw):
        async def go():
            handle, got = self.cap_server(ack_lines, **kw)
            srv = await asyncio.start_server(handle, '127.0.0.1', 0)
            bot = make_bot(srv.sockets[0].getsockname()[1], sasl=('acct', 'pw'))
            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: bot.connected, 5)
                await asyncio.sleep(0.2)
                return got, set(bot.caps)
            finally:
                await bot.shutdown(timeout=2)
                task.cancel()
                srv.close()
        return self.run_async(go())

    def assert_cap_order(self, got):
        """One CAP END, after SASL finished; AUTHENTICATE only after every ACK."""
        self.assertEqual(got.count('CAP END'), 1, got)
        auth = got.index('AUTHENTICATE PLAIN')
        self.assertLess(auth, got.index('CAP END'), got)
        self.assertTrue(any(l.startswith('AUTHENTICATE ') and l != 'AUTHENTICATE PLAIN' for l in got[auth:]), got)

    def test_cap_ack_split_with_continuation(self):
        got, caps = self.run_cap([':srv CAP TestBot ACK * :account-tag multi-prefix', ':srv CAP TestBot ACK :sasl'])
        self.assert_cap_order(got)
        self.assertTrue({'sasl', 'account-tag', 'multi-prefix'} <= caps)

    def test_cap_ack_in_separate_answers(self):
        got, caps = self.run_cap([':srv CAP TestBot ACK :account-tag', ':srv CAP TestBot ACK :multi-prefix',
                                  ':srv CAP TestBot ACK :sasl'])
        self.assert_cap_order(got)
        self.assertTrue({'sasl', 'account-tag', 'multi-prefix'} <= caps)

    def test_cap_ls_over_several_lines_and_nak(self):
        got, caps = self.run_cap([':srv CAP TestBot NAK :account-tag multi-prefix sasl'],
                                 ls_lines=(':srv CAP * LS * :account-tag multi-prefix', ':srv CAP * LS :sasl'))
        self.assertEqual(got.count('CAP END'), 1, got)
        self.assertEqual([l for l in got if l.startswith('CAP REQ')], ['CAP REQ :account-tag multi-prefix sasl'])
        self.assertNotIn('AUTHENTICATE PLAIN', got)               # refused: no SASL, registration goes on
        self.assertFalse(caps & {'sasl', 'account-tag'})

    def test_reconnects_and_rejoins(self):
        async def go():
            s = await IRCServer('plain').start()
            bot = make_bot(s.port)
            bot.reconnect_delay = 0.2
            task = asyncio.ensure_future(bot.run_forever())
            try:
                await until(lambda: s.chan('#test') and 'testbot' in s.chan('#test').members, 10)
                first = s.client('TestBot')
                s.kill('TestBot', 'test')
                # a new connection (the bot may be back before a check could see it gone), in the channel again
                await until(lambda: s.client('TestBot') not in (None, first) and s.chan('#test') and
                            'testbot' in s.chan('#test').members, 10)
            finally:
                bot.stop()
                task.cancel()
                await s.stop()
        self.run_async(go())

    def test_stop_closes_even_when_the_server_ignores_quit(self):
        async def go():
            got = []

            async def deaf(reader, writer):                   # reads everything, answers nothing, never closes
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    got.append(line.decode().strip())
            srv = await asyncio.start_server(deaf, '127.0.0.1', 0)
            bot = make_bot(srv.sockets[0].getsockname()[1])
            task = asyncio.ensure_future(bot.run_forever())
            await until(lambda: any(l.startswith('USER') for l in got), 5)
            t0 = time.monotonic()
            await bot.shutdown('Goodbye', timeout=3)
            self.assertTrue(task.done(), 'run_forever is still running')
            self.assertLess(time.monotonic() - t0, 4)
            self.assertIn('QUIT Goodbye', got)
            srv.close()
        self.run_async(go())

    def test_stop_during_the_reconnect_wait(self):
        async def go():
            srv = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)   # hangs up at once
            bot = make_bot(srv.sockets[0].getsockname()[1])
            bot.reconnect_delay = 60
            task = asyncio.ensure_future(bot.run_forever())
            await asyncio.sleep(0.5)                           # now in the 60 s wait
            t0 = time.monotonic()
            await bot.shutdown()
            self.assertTrue(task.done())
            self.assertLess(time.monotonic() - t0, 1)
            srv.close()
        self.run_async(go())


if __name__ == '__main__':
    unittest.main()

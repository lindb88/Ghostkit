"""irclab.client: scripted IRC users, to talk to a bot under test and check what it answers.

    alice = await User('Alice').connect(server.port)
    await alice.join('#test')
    alice.say('#test', '!hello')
    line = await alice.reply('MyBot', 'Hello, Alice', where='#test')     # waits, or raises AssertionError
    r = await alice.ask('MyBot', 'help')                                 # a private message and its answer

Every line a user receives is kept in .lines; .pos is where the next wait starts looking, and mark() moves it
to the end, so a wait only sees what comes after. Waits time out with the last lines in the error, which is
usually all you need to see why. Part of ghostkit (MIT license).
"""

import asyncio
import re
import time

from .server import low, q_expected


class Line:
    """One IRC line, split: .tags, .prefix, .nick, .command, .params, .text (the last parameter)."""

    def __init__(self, raw):
        self.raw = raw
        m = re.match(r'^(?:@(\S+) )?(?::(\S+) )?(\S+)(.*)$', raw)
        self.tags, self.prefix, self.command, rest = m.group(1) or '', m.group(2) or '', m.group(3), m.group(4)
        self.nick = self.prefix.split('!')[0]
        if ' :' in rest:
            head, trail = rest.split(' :', 1)
            self.params = head.split() + [trail]
        else:
            self.params = rest.split()
        self.text = self.params[-1] if self.params else ''

    @property
    def target(self):
        """Where a PRIVMSG or NOTICE went: a channel, or a nick."""
        return self.params[0] if self.params else ''

    def __repr__(self):
        return self.raw


class User:
    """A scripted IRC user. The host the server gives it is <ident>.host."""

    def __init__(self, nick, ident=None, realname='irclab user'):
        self.nick, self.ident, self.realname = nick, ident or nick.lower(), realname
        self.lines = []
        self.pos = 0
        self.event = asyncio.Event()
        self.task = None

    async def connect(self, port, host='127.0.0.1'):
        self.reader, self.writer = await asyncio.open_connection(host, port)
        self.task = asyncio.ensure_future(self._read())
        self.send('NICK %s' % self.nick)
        self.send('USER %s 0 * :%s' % (self.ident, self.realname))
        await self.expect(lambda l: l.command == '001', 10, 'the welcome (001)')
        return self

    async def _read(self):
        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break
                l = Line(line.decode('utf-8', 'replace').rstrip('\r\n'))
                if l.command == 'PING':
                    self.send('PONG :%s' % l.text)
                elif l.command == 'NICK' and low(l.nick) == low(self.nick):
                    self.nick = l.text
                self.lines.append(l)
                self.event.set()
        except Exception:
            pass

    # ---- talking ----
    def send(self, line):
        self.writer.write((line + '\r\n').encode())

    def say(self, target, text):
        self.send('PRIVMSG %s :%s' % (target, text))

    def notice(self, target, text):
        self.send('NOTICE %s :%s' % (target, text))

    async def join(self, chan, key=None):
        self.send('JOIN %s%s' % (chan, ' ' + key if key else ''))
        await self.expect(lambda l: l.command == '366' and low(l.params[1]) == low(chan), 10, 'joining ' + chan)

    def part(self, chan, reason=''):
        self.send('PART %s :%s' % (chan, reason))

    def mark(self):
        """Waits from now on look only at lines that come after this."""
        self.pos = len(self.lines)

    # ---- waiting ----
    async def expect(self, pred, timeout=10, what=None):
        """The next line (from .pos) for which pred(line) is true; AssertionError after timeout seconds."""
        t = time.time()
        while True:
            while self.pos < len(self.lines):
                l = self.lines[self.pos]
                self.pos += 1
                if pred(l):
                    return l
            left = timeout - (time.time() - t)
            if left <= 0:
                recent = '\n'.join(x.raw for x in self.lines[-15:])
                raise AssertionError('%s: timed out waiting for %s\n--- the last lines it got ---\n%s' % (
                    self.nick, what or 'a line', recent))
            self.event.clear()
            try:
                await asyncio.wait_for(self.event.wait(), left)
            except asyncio.TimeoutError:
                pass

    async def reply(self, frm, contains=None, kind=None, timeout=15, where=None):
        """A PRIVMSG or NOTICE from nick frm (to me, or to where, a channel) containing contains."""
        def pred(l):
            if l.command not in ('PRIVMSG', 'NOTICE') or low(l.nick) != low(frm):
                return False
            if kind and l.command != kind:
                return False
            if where and low(l.params[0]) != low(where):
                return False
            return contains is None or contains in l.text
        return await self.expect(pred, timeout, '%s %sfrom %s containing %r' % (
            kind or 'a message', ('in %s ' % where) if where else '', frm, contains))

    async def ask(self, bot, text, contains=None, timeout=15, pace=1.3):
        """Say text to bot privately and wait for its answer. Waits pace seconds after the last ask, as bots
        often ignore people who type many commands a second."""
        wait = getattr(self, '_last_ask', 0) + pace - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_ask = time.time()
        self.mark()
        self.say(bot, text)
        return await self.reply(bot, contains, timeout=timeout)

    # ---- services ----
    async def q_auth(self, account, password, hide=True):
        """Log in to QuakeNet's Q (mode 'quakenet'), and hide the host (+x)."""
        self.say('Q@CServe.quakenet.org', 'CHALLENGE')
        l = await self.expect(lambda l: l.text.startswith('CHALLENGE '), 10, "Q's challenge")
        self.say('Q@CServe.quakenet.org', 'CHALLENGEAUTH %s %s HMAC-SHA-256' % (
            account, q_expected(account, password, l.text.split()[1])))
        await self.expect(lambda l: 'logged in as' in l.text, 10, 'the Q login')
        if hide:
            self.send('MODE %s +x' % self.nick)
            await self.expect(lambda l: l.command == '396', 10, 'the hidden host')

    async def identify(self, account, password):
        """Log in to NickServ (mode 'libera')."""
        self.say('NickServ', 'IDENTIFY %s %s' % (account, password))
        await self.expect(lambda l: 'identified for' in l.text, 10, 'the NickServ login')

    def close(self, reason='bye'):
        try:
            self.send('QUIT :%s' % reason)
            self.writer.close()
            if self.task:
                self.task.cancel()
        except Exception:
            pass


async def until(cond, timeout=20, what='the condition'):
    """Wait until cond() is true (errors count as not yet); AssertionError after timeout seconds."""
    t = time.time()
    while time.time() - t < timeout:
        try:
            if cond():
                return True
        except Exception:
            pass
        await asyncio.sleep(0.2)
    raise AssertionError('timed out waiting for ' + what)

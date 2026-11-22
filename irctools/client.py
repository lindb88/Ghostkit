"""irctools.client: a small IRC bot base that does the housekeeping, so a bot is mostly its commands.

    from irctools import Bot

    bot = Bot('EchoBot', 'irc.libera.chat', 6697, tls=True, channels=['#mytest'])

    @bot.command('!echo')
    async def echo(ctx):
        ctx.reply(ctx.args or 'echo what?')

    @bot.on('JOIN')
    async def hello(bot, msg):
        if not bot.is_me(msg.nick):
            bot.say(msg.target, 'Welcome, %s!' % msg.nick)

    bot.run()

What it takes care of: TLS; IRCv3 capabilities (CAP LS 302: sasl, account-tag, extended-join, multi-prefix,
away-notify, account-notify, server-time, message-tags, whichever the server offers); SASL PLAIN; another nick
when the nick is taken; the server's ISUPPORT (case rules, channel types, prefixes); PING/PONG, and a PING of
its own when the server goes quiet; reconnecting with growing waits (and a longer one when throttled); joining
its channels, and again after a reconnect; who is in each channel; flood-safe sending (SendQueue); a reply
never contains a line break, so users cannot make the bot send IRC lines of their own.
"""

import asyncio
import json
import logging
import os
import random
import ssl
import time

from .casemap import CaseDict, equal, lower
from .masks import is_channel
from .message import build, clean, parse, split_text
from .sendq import SendQueue

log = logging.getLogger('irctools')
CAPS_WANTED = ('account-tag', 'extended-join', 'multi-prefix', 'away-notify', 'account-notify', 'server-time',
               'message-tags')


class Context:
    """A command as typed: .nick, .msg, .args (the rest of the line), .where (the channel, or None if private)."""

    def __init__(self, bot, msg, args):
        self.bot, self.msg, self.args = bot, msg, args
        self.nick = msg.nick
        self.where = msg.target if is_channel(msg.target, bot.chantypes) else None
        self.account = msg.tags.get('account') or bot.accounts.get(msg.nick)

    def reply(self, text):
        """In the channel it was typed in, or privately."""
        self.bot.say(self.where or self.nick, text)

    def tell(self, text):
        """Only to the person: a notice in a channel, a message in private."""
        (self.bot.notice if self.where else self.bot.say)(self.nick, text)


class Channel:
    def __init__(self, name):
        self.name = name
        self.users = {}                                # lower nick -> prefix characters ('@', '+')
        self.topic = ''


class Bot:
    def __init__(self, nick, host, port=6667, tls=False, channels=(), sasl=None, realname='irctools bot',
                 username=None, password=None, prefix_chars='!', tls_verify=True, burst=4, interval=1.5):
        self.nick, self.want_nick = nick, nick
        self.host, self.port, self.tls, self.tls_verify = host, port, tls, tls_verify
        self.channels_wanted = list(channels)
        self.sasl = sasl                                # (account, password) for SASL PLAIN
        self.realname, self.username, self.password = realname, username or nick.lower(), password
        self.prefix_chars = prefix_chars
        self.burst, self.interval = burst, interval
        self.handlers = {}                              # COMMAND -> [async fn(bot, msg)]
        self.commands = {}                              # '!word' -> async fn(ctx)
        self.casemap, self.chantypes, self.prefixes = 'rfc1459', '#&', {'o': '@', 'v': '+'}
        self.channels = CaseDict(self.casemap)
        self.accounts = CaseDict(self.casemap)          # nick -> account (from account-notify, extended-join)
        self.caps = set()
        self.connected = False
        self.running = True
        self.started, self.since = time.time(), None   # the program's start; connected since (None: not now)
        self._status = None                             # (path, every, options) from status_file()
        self.q = None
        self._last_rx = 0.0

    # ---- what a bot adds ----
    def on(self, command):
        """@bot.on('PRIVMSG'): an async fn(bot, msg) for each such line (numerics too: '001', '433')."""
        def deco(fn):
            self.handlers.setdefault(command.upper(), []).append(fn)
            return fn
        return deco

    def command(self, word):
        """@bot.command('!hello'): an async fn(ctx) when someone types it (in a channel or privately)."""
        def deco(fn):
            self.commands[word.lower()] = fn
            return fn
        return deco

    # ---- saying things ----
    def send(self, line, urgent=False):
        if self.q is not None:
            self.q.put(clean(line), urgent)

    def say(self, target, text):
        for piece in split_text(text):
            self.send(build('PRIVMSG', target, piece))

    def notice(self, target, text):
        for piece in split_text(text):
            self.send(build('NOTICE', target, piece))

    def join(self, chan, key=None):
        self.send(build('JOIN', chan, key) if key else build('JOIN', chan))

    def part(self, chan, reason=''):
        self.send(build('PART', chan, reason))

    def lower(self, s):
        return lower(s, self.casemap)

    def is_me(self, nick):
        return equal(nick, self.nick, self.casemap)

    def is_op(self, chan, nick=None):
        c = self.channels.get(chan)
        return bool(c) and '@' in c.users.get(self.lower(nick or self.nick), '')

    # ---- a status page (webstatus) ----
    def status(self, hide=(), network=None, version=None, note=None):
        """This bot in webstatus's format: name, connected, channels with their user counts and whether it has ops.
        Channels in hide are left out (secret ones)."""
        hidden = {self.lower(h) for h in hide}
        b = {'name': self.nick, 'connected': self.connected, 'server': self.host, 'started': int(self.started),
             'channels': [{'name': c.name, 'users': len(c.users), 'op': self.is_op(c.name)}
                          for k, c in sorted(self.channels.items()) if k not in hidden]}
        if self.connected and self.since:
            b['since'] = int(self.since)
        for k, v in (('network', network), ('version', version), ('note', note)):
            if v:
                b[k] = v
        return b

    def status_file(self, path, every=30, title=None, **options):
        """Write status.json for webstatus's page every so many seconds while running (and when stopping).
        options: hide=[...], network=..., version=..., note=... (as status())."""
        self._status = (path, every, title, options)

    def write_status(self):
        if not self._status:
            return
        path, _, title, options = self._status
        data = {'format': 1, 'updated': int(time.time()), 'bots': [self.status(**options)]}
        if title:
            data['title'] = title
        folder = os.path.dirname(os.path.abspath(path))
        tmp = os.path.join(folder, '.%s.%d.tmp' % (os.path.basename(path), os.getpid()))
        try:
            os.makedirs(folder, exist_ok=True)
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)                       # the page never reads half a file
        except OSError as e:
            log.warning('could not write %s: %s', path, e)

    async def _status_loop(self):
        while self.running:
            self.write_status()
            await asyncio.sleep(self._status[1])

    # ---- running ----
    def run(self):
        """Connect, and stay connected until stop() (or Ctrl-C)."""
        try:
            asyncio.run(self.run_forever())
        except KeyboardInterrupt:
            pass

    def stop(self, reason='Bye'):
        self.running = False
        if self.q is not None:
            self.q.clear()
            self.send(build('QUIT', reason), urgent=True)

    async def run_forever(self):
        delay = 5
        writer = asyncio.ensure_future(self._status_loop()) if self._status else None
        try:
            await self._run_forever(delay)
        finally:
            if writer:
                writer.cancel()
                self.write_status()                     # the last word: not connected

    async def _run_forever(self, delay):
        while self.running:
            started = time.time()
            throttled = False
            try:
                throttled = await self.session()
            except (OSError, asyncio.TimeoutError, ConnectionError, ssl.SSLError) as e:
                log.warning('connection: %s', e)
            self.connected, self.since = False, None
            if self._status:
                self.write_status()
            if not self.running:
                break
            if time.time() - started > 300:
                delay = 5                                # it was up a while: start over with short waits
            wait = max(delay, 90) if throttled else delay
            wait *= random.uniform(0.8, 1.2)              # many bots on one host do not come back at once
            log.info('reconnecting in %.0f s', wait)
            await asyncio.sleep(wait)
            delay = min(delay * 2, 300)

    async def session(self):
        """One connection, until it ends. True when the server said we reconnect too fast."""
        ctx = None
        if self.tls:
            ctx = ssl.create_default_context()
            if not self.tls_verify:
                ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx, server_hostname=self.host if ctx else None), 30)
        self.q = SendQueue(writer, self.burst, self.interval, on_send=lambda l: log.debug('>> %s', l))
        sender = asyncio.ensure_future(self.q.run())
        keepalive = asyncio.ensure_future(self._keepalive(writer))
        self.nick, self.caps, self.channels = self.want_nick, set(), CaseDict(self.casemap)
        self._sasl_done = not self.sasl
        self._last_rx = time.monotonic()                  # the keepalive counts from the connection's start
        throttled = False
        try:
            self.send('CAP LS 302', urgent=True)
            if self.password:
                self.send(build('PASS', self.password), urgent=True)
            self.send(build('NICK', self.nick), urgent=True)
            self.send(build('USER', self.username, '0', '*', self.realname), urgent=True)
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                self._last_rx = time.monotonic()
                line = raw.decode('utf-8', 'replace').rstrip('\r\n')
                if not line:
                    continue
                log.debug('<< %s', line)
                msg = parse(line)
                if msg.command == 'ERROR' and 'too fast' in msg.text.lower():
                    throttled = True
                await self._handle(msg)
        finally:
            self.q.close()
            for t in (sender, keepalive):
                t.cancel()
            try:
                writer.close()
            except Exception:
                pass
        return throttled

    async def _keepalive(self, writer):
        """A PING when the server has been quiet 2 minutes; the connection ends if 1 more passes in silence."""
        while True:
            await asyncio.sleep(30)
            quiet = time.monotonic() - self._last_rx
            if quiet > 180:
                log.warning('the server has said nothing for 3 minutes; reconnecting')
                writer.close()
                return
            if quiet > 120:
                self.send('PING :keepalive', urgent=True)

    async def _handle(self, msg):
        c, p = msg.command, msg.params
        if c == 'PING':
            self.send(build('PONG', msg.text), urgent=True)
        elif c == 'CAP':
            await self._cap(msg)
        elif c == 'AUTHENTICATE' and msg.text == '+' and self.sasl:
            import base64
            token = base64.b64encode(('%s\0%s\0%s' % (self.sasl[0], self.sasl[0], self.sasl[1])).encode()).decode()
            self.send('AUTHENTICATE ' + token, urgent=True)
        elif c in ('903', '904', '905', '906', '907'):
            if c != '903':
                log.warning('SASL failed (%s)', msg.text)
            self._sasl_done = True
            self.send('CAP END', urgent=True)
        elif c == '001':
            self.connected = True
            self.since = time.time()
            self.nick = msg.arg(0, self.nick)
        elif c == '005':
            self._isupport(p[1:-1])
        elif c in ('376', '422'):                          # the end of the welcome: join
            for ch in self.channels_wanted:
                self.join(ch)
        elif c == '433' and not self.connected:            # the nick is taken
            self.nick = (self.nick + '_')[:15] if len(self.nick) < 15 else self.nick[:-1] + str(random.randint(0, 9))
            self.send(build('NICK', self.nick), urgent=True)
        elif c == 'NICK':
            self._nick(msg)
        elif c == 'JOIN':
            self._join(msg)
        elif c == 'PART':
            self._gone(msg.target, msg.nick)
        elif c == 'KICK':
            self._gone(msg.target, msg.arg(1))
            if self.is_me(msg.arg(1)) and any(equal(msg.target, w, self.casemap) for w in self.channels_wanted):
                asyncio.get_event_loop().call_later(10, self.join, msg.target)   # back in after a kick
        elif c == 'QUIT':
            for ch in self.channels.values():
                ch.users.pop(self.lower(msg.nick), None)
            self.accounts.pop(msg.nick, None)
        elif c == '353':
            self._names(msg)
        elif c == 'MODE':
            self._mode(msg)
        elif c == 'ACCOUNT':
            self.accounts[msg.nick] = '' if msg.text == '*' else msg.text
        elif c == 'TOPIC' or c == '332':
            ch = self.channels.get(msg.target if c == 'TOPIC' else msg.arg(1))
            if ch:
                ch.topic = msg.text
        elif c == 'PRIVMSG':
            await self._command(msg)
        for fn in self.handlers.get(c, []) + self.handlers.get('*', []):
            try:
                await fn(self, msg)
            except Exception:
                log.exception('a handler for %s failed', c)

    async def _cap(self, msg):
        sub = msg.arg(1).upper()
        if sub == 'LS':
            offered = {x.split('=', 1)[0] for x in msg.text.split()}
            if msg.arg(2) == '*':                           # more LS lines to come
                self._offered = getattr(self, '_offered', set()) | offered
                return
            offered |= getattr(self, '_offered', set())
            self._offered = set()
            want = [x for x in CAPS_WANTED if x in offered] + (['sasl'] if self.sasl and 'sasl' in offered else [])
            if want:
                self.send(build('CAP', 'REQ', ' '.join(want)), urgent=True)
            else:
                self.send('CAP END', urgent=True)
        elif sub == 'ACK':
            self.caps |= set(msg.text.split())
            if 'sasl' in self.caps and not self._sasl_done:
                self.send('AUTHENTICATE PLAIN', urgent=True)
            else:
                self.send('CAP END', urgent=True)
        elif sub == 'NAK':
            self.send('CAP END', urgent=True)

    def _isupport(self, tokens):
        for t in tokens:
            k, _, v = t.partition('=')
            if k == 'CASEMAPPING' and v:
                self.casemap = v.lower()
                self.channels.mapping = self.accounts.mapping = self.casemap
            elif k == 'CHANTYPES' and v:
                self.chantypes = v
            elif k == 'PREFIX' and v.startswith('('):
                modes, _, chars = v[1:].partition(')')
                self.prefixes = dict(zip(modes, chars))

    def _nick(self, msg):
        old, new = msg.nick, msg.text
        if self.is_me(old):
            self.nick = new
        for ch in self.channels.values():
            if self.lower(old) in ch.users:
                ch.users[self.lower(new)] = ch.users.pop(self.lower(old))
        if old in self.accounts:
            self.accounts[new] = self.accounts.pop(old)

    def _join(self, msg):
        chan = msg.target
        if self.is_me(msg.nick):
            self.channels[chan] = Channel(chan)
        ch = self.channels.get(chan)
        if ch is not None:
            ch.users[self.lower(msg.nick)] = ''
        if len(msg.params) >= 2 and 'extended-join' in self.caps:
            self.accounts[msg.nick] = '' if msg.arg(1) == '*' else msg.arg(1)

    def _gone(self, chan, nick):
        if self.is_me(nick):
            self.channels.pop(chan, None)
            return
        ch = self.channels.get(chan)
        if ch:
            ch.users.pop(self.lower(nick), None)

    def _names(self, msg):
        ch = self.channels.get(msg.arg(2))
        if not ch:
            return
        marks = ''.join(self.prefixes.values())
        for item in msg.text.split():
            i = 0
            while i < len(item) and item[i] in marks:
                i += 1
            ch.users[self.lower(item[i:].split('!')[0])] = item[:i]

    def _mode(self, msg):
        ch = self.channels.get(msg.target)
        if not ch or len(msg.params) < 2:
            return
        sign, args = '+', list(msg.params[2:])
        for m in msg.params[1]:
            if m in '+-':
                sign = m
            elif m in self.prefixes:
                if not args:
                    break
                who = self.lower(args.pop(0))
                cur = ch.users.get(who, '')
                mark = self.prefixes[m]
                ch.users[who] = (cur + mark) if sign == '+' and mark not in cur else cur.replace(mark, '') \
                    if sign == '-' else cur
            elif m in 'bkleIq' and args and (sign == '+' or m in 'bkeIq'):
                args.pop(0)                             # modes with an argument (k on -k too, on most servers)

    async def _command(self, msg):
        text = msg.text
        if not text or text[0] not in self.prefix_chars and is_channel(msg.target, self.chantypes):
            return
        word, _, args = text.strip().partition(' ')
        fn = self.commands.get(word.lower())
        if fn is None and not is_channel(msg.target, self.chantypes):
            fn = self.commands.get(self.prefix_chars[0] + word.lower())     # in private, the ! may be left out
        if fn:
            try:
                await fn(Context(self, msg, args.strip()))
            except Exception:
                log.exception('the command %s failed', word)

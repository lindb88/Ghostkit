"""irclab.scenario: test a bot written in any language with a plain text file.

    # hello.scenario
    bot python3 mybot.py --server 127.0.0.1 --port {port}
    wait MyBot in #test
    user Alice
    Alice join #test
    Alice say #test !hello
    expect Alice from MyBot in #test "Hello, Alice"
    refute Alice from MyBot in #test for 2

    $ python3 -m irclab run hello.scenario
    ok 1 - bot python3 mybot.py ...
    ...
    6 of 6 passed

One step per line; a line starting with # is a comment (a # later on is a channel name); quotes group words ("Hello, Alice"). Steps:

  server <mode>                         plain (default), libera or quakenet; first, if at all
  account <name> <password>             a services account (libera, quakenet)
  chanlev <#chan> <account> <flags>     services access in a channel (ChanServ / Q op and invite)
  bot <command> [args]                  start the bot; {port} and {host} are the server's; its output is
                                        kept and shown when a step fails. It runs in the scenario's
                                        folder; words are taken as the scenario reads them (quotes group,
                                        no pipes or &&: for more, start a script)
  user <nick> [ident]                   a user connects (its host is <ident>.host)
  <nick> join <#chan> [key]             ... and the other things a user does:
  <nick> part <#chan> | say <target> <text> | notice <target> <text> | send <raw line>
  <nick> nick <newnick> | quit | identify <account> <password> | qauth <account> <password>
  wait <nick> in <#chan>                until that nick (the bot) is in the channel
  wait <nick> op <#chan>                until it has ops there
  wait <nick> online                    until it is connected
  expect <user> from <nick> [in <#chan> | private] [notice] [<text>]
                                        the user gets a message from nick (containing text)
  refute <user> from <nick> [in <#chan> | private] [<text>] for <seconds>
                                        ... and gets no such message for that long
  sleep <seconds>
Waits and expects give up after 10 seconds; end a step with "within <seconds>" for another limit.
Exit status 0 when every step passed. Part of ghostkit (MIT license).
"""

import asyncio
import os
import shlex
import signal
import subprocess
import tempfile
import time

from .client import User, until
from .server import IRCServer, low


class StepFailed(Exception):
    pass


def parse(text):
    """[(line number, tokens)] for a scenario's text."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            toks = shlex.split(line)
        except ValueError as e:
            raise StepFailed('line %d: %s' % (n, e))
        if toks:
            out.append((n, toks))
    return out


def take_within(toks, default=10.0):
    if len(toks) >= 2 and toks[-2] == 'within':
        return toks[:-2], float(toks[-1])
    return toks, default


class Runner:
    def __init__(self, text, out=print, verbose=False, base=None, on_server=None, env=None):
        """on_server(server): called once the server listens (its .port known), before any step: for a tool
        that writes the bot's config. env: more environment variables for the bot."""
        self.steps = parse(text)
        self.out, self.verbose = out, verbose
        self.base = base or os.getcwd()               # the bot command runs here (the scenario's folder)
        self.on_server, self.env = on_server, env
        self.server = None
        self.users = {}
        self.bot = None
        self.botlog = None

    async def run(self):
        mode = 'plain'
        if self.steps and self.steps[0][1][0] == 'server':
            mode = self.steps[0][1][1] if len(self.steps[0][1]) > 1 else 'plain'
        self.server = await IRCServer(mode).start()
        if self.on_server:
            self.on_server(self.server)
        passed = failed = 0
        try:
            for i, (n, toks) in enumerate(self.steps, 1):
                label = ' '.join(shlex.quote(t) if ' ' in t else t for t in toks)
                try:
                    await self.step(toks)
                    passed += 1
                    self.out('ok %d - %s' % (i, label))
                except (StepFailed, AssertionError, OSError, ValueError, IndexError) as e:
                    failed += 1
                    self.out('not ok %d - %s (line %d)' % (i, label, n))
                    for l in str(e).splitlines() or ['(no detail)']:
                        self.out('#   ' + l)
                    self.out('#   --- the bot\'s last output ---')
                    for l in self.bot_tail():
                        self.out('#   ' + l)
                    break                              # later steps build on this one
        finally:
            await self.close()
        skipped = len(self.steps) - passed - failed
        self.out('%d of %d passed%s' % (passed, len(self.steps), ', %d not run' % skipped if skipped else ''))
        return failed == 0

    def bot_tail(self, n=15):
        if not self.botlog:
            return ['(no bot started)']
        try:
            with open(self.botlog.name, errors='replace') as f:
                return f.read().splitlines()[-n:] or ['(nothing yet)']
        except OSError:
            return ['(no output)']

    async def step(self, toks):
        word = toks[0]
        s = self.server
        if word == 'server':
            return
        if word == 'account':
            s.add_account(toks[1], toks[2])
        elif word == 'chanlev':
            s.set_chanlev(toks[1], toks[2], toks[3])
        elif word == 'bot':
            cmd = ' '.join(shlex.quote(t.replace('{port}', str(s.port)).replace('{host}', '127.0.0.1')) for t in toks[1:])
            self.botlog = tempfile.NamedTemporaryFile('w', prefix='irclab-bot-', suffix='.log', delete=False)
            env = dict(os.environ, **(self.env or {}))
            self.bot = subprocess.Popen(cmd, shell=True, cwd=self.base, env=env, stdout=self.botlog,
                                        stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        elif word == 'user':
            u = User(toks[1], toks[2] if len(toks) > 2 else None)
            await u.connect(s.port)
            self.users[low(toks[1])] = u
        elif word == 'sleep':
            await asyncio.sleep(float(toks[1]))
        elif word == 'wait':
            toks, within = take_within(toks)
            nick, what = toks[1], toks[2]
            if what == 'online':
                await self.wait(lambda: s.client(nick), within, '%s to connect' % nick)
            elif what in ('in', 'op'):
                chan = toks[3]
                if what == 'in':
                    ok = lambda: s.chan(chan) and low(nick) in s.chan(chan).members      # noqa: E731
                else:
                    ok = lambda: s.is_op(chan, nick)                                     # noqa: E731
                await self.wait(ok, within, '%s %s %s' % (nick, 'to be in' if what == 'in' else 'to have ops in',
                                                          chan))
            else:
                raise StepFailed('wait: in, op or online?')
        elif word in ('expect', 'refute'):
            await self.check(word, toks)
        elif low(word) in self.users:
            await self.act(self.users[low(word)], toks[1:])
        else:
            raise StepFailed('what is "%s"? (a step, or a user made with: user %s)' % (word, word))

    async def wait(self, cond, within, what):
        if self.bot is not None and self.bot.poll() is not None:
            raise StepFailed('the bot has exited (status %s)' % self.bot.returncode)
        try:
            await until(cond, within, what)
        except AssertionError:
            if self.bot is not None and self.bot.poll() is not None:
                raise StepFailed('the bot exited (status %s) while waiting for %s' % (self.bot.returncode, what))
            raise StepFailed('timed out after %gs waiting for %s' % (within, what))

    async def act(self, u, toks):
        verb = toks[0]
        if verb == 'join':
            await u.join(toks[1], toks[2] if len(toks) > 2 else None)
        elif verb == 'part':
            u.part(toks[1])
        elif verb == 'say':
            u.say(toks[1], ' '.join(toks[2:]))
        elif verb == 'notice':
            u.notice(toks[1], ' '.join(toks[2:]))
        elif verb == 'send':
            u.send(' '.join(toks[1:]))
        elif verb == 'nick':
            u.send('NICK %s' % toks[1])
            await u.expect(lambda l: l.command == 'NICK' and low(l.text) == low(toks[1]), 10, 'the nick change')
        elif verb == 'quit':
            u.close()
            self.users.pop(low(u.nick), None)
        elif verb == 'identify':
            await u.identify(toks[1], toks[2])
        elif verb == 'qauth':
            await u.q_auth(toks[1], toks[2])
        else:
            raise StepFailed('a user can join, part, say, notice, send, nick, quit, identify or qauth')

    async def check(self, word, toks):
        """expect/refute <user> from <nick> [in <#chan> | private] [notice] [<text>] [within N | for N]"""
        if word == 'refute':
            if len(toks) < 3 or toks[-2] != 'for':
                raise StepFailed('refute ... for <seconds>')
            toks, within = toks[:-2], float(toks[-1])
        else:
            toks, within = take_within(toks)
        u = self.users.get(low(toks[1]))
        if not u or len(toks) < 4 or toks[2] != 'from':
            raise StepFailed('%s <user> from <nick> ...' % word)
        frm, rest = toks[3], toks[4:]
        where, kind = None, None
        if rest[:1] == ['in']:
            where, rest = rest[1], rest[2:]
        elif rest[:1] == ['private']:
            where, rest = u.nick, rest[1:]
        if rest[:1] == ['notice']:
            kind, rest = 'NOTICE', rest[1:]
        text = ' '.join(rest) or None
        if word == 'expect':
            if self.bot is not None and self.bot.poll() is not None:
                raise StepFailed('the bot has exited (status %s)' % self.bot.returncode)
            await u.reply(frm, text, kind=kind, timeout=within, where=where)
            return
        u.mark()
        start = time.time()
        await asyncio.sleep(within)
        for l in u.lines[u.pos:]:
            if l.command in ('PRIVMSG', 'NOTICE') and low(l.nick) == low(frm) and \
                    (where is None or low(l.target) == low(where)) and (kind is None or l.command == kind) and \
                    (text is None or text in l.text):
                raise StepFailed('it did say: %s (%.1fs in)' % (l.raw, time.time() - start))

    async def close(self):
        for u in list(self.users.values()):
            u.close()
        if self.bot is not None and self.bot.poll() is None:
            try:
                os.killpg(self.bot.pid, signal.SIGTERM)
                for _ in range(20):
                    if self.bot.poll() is not None:
                        break
                    await asyncio.sleep(0.1)
                if self.bot.poll() is None:
                    os.killpg(self.bot.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if self.botlog:
            self.botlog.close()
            if self.verbose:
                self.out('# the bot\'s output: %s' % self.botlog.name)
            else:
                try:
                    os.unlink(self.botlog.name)
                except OSError:
                    pass
        if self.server:
            await self.server.stop()


def run_file(path, verbose=False):
    with open(path, encoding='utf-8') as f:
        text = f.read()
    r = Runner(text, verbose=verbose, base=os.path.dirname(os.path.abspath(path)))
    return asyncio.run(r.run())

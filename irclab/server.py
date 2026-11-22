"""irclab.server: a small IRC server for testing bots, on your own machine.

    server = await IRCServer('plain').start()      # a free port on 127.0.0.1: server.port
    ...
    await server.stop()

Modes:
  'plain'     no services; CAP LS 302 with account-notify, extended-join, account-tag, multi-prefix and
              away-notify (like most modern networks without services)
  'libera'    as 'plain', plus SASL (PLAIN, EXTERNAL with client certificates over TLS), NickServ
              (IDENTIFY, REGISTER, VERIFY, CERT ADD, REGAIN) and ChanServ (OP, INVITE, UNBAN by flags)
  'quakenet'  no CAP (like ircu); Q (CHALLENGE/CHALLENGEAUTH, HELLO, OP, INVITE, UNBANME) and +x hidden
              hosts; users start invisible (+i)

Every user gets the host <ident>.host, so masks are predictable. Channel modes b, i, k, l, m, n, o, p, r, s,
t and v; WHO (with WHOX), WHOIS, NAMES, INVITE, KICK, TOPIC, ISON. Everything the server receives is kept in
server.received as (nick, line) for checks. The first user in a channel gets ops.

Test helpers: add_account(name, password), set_chanlev(chan, account, flags) (services access), client(nick),
chan(name), is_op(chan, nick), kill(nick), banned_hosts (K-lines), throttle (seconds between connections).
Part of ghostkit (MIT license).
"""

import asyncio
import base64
import hashlib
import hmac
import re
import secrets
import ssl
import time

RFC = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\~', 'abcdefghijklmnopqrstuvwxyz{}|^')


def low(s):
    return s.translate(RFC)


def wild(pattern, text):
    p = re.escape(low(pattern)).replace(r'\*', '.*').replace(r'\?', '.')
    return re.match('^' + p + '$', low(text)) is not None


def q_expected(account, password, challenge):
    """QuakeNet's documented CHALLENGEAUTH response (HMAC-SHA-256), for Q logins."""
    passhash = hashlib.sha256(password[:10].encode()).hexdigest()
    key = hashlib.sha256((low(account) + ':' + passhash).encode()).hexdigest()
    return hmac.new(key.encode(), challenge.encode(), hashlib.sha256).hexdigest()


class Client:
    def __init__(self, server, reader, writer):
        self.s, self.reader, self.writer = server, reader, writer
        self.nick = self.user = self.real = ''
        self.host = ''
        self.registered = False
        self.cap_negotiating = False
        self.caps = set()
        self.account = ''
        self.modes = set()
        self.challenge = None
        self.sasl = None
        self.certfp = ''

    @property
    def mask(self):
        return '%s!%s@%s' % (self.nick, self.user, self.host)

    def send(self, line):
        try:
            self.writer.write((line + '\r\n').encode())
        except Exception:
            pass

    def num(self, code, *params):
        args = list(params)
        if args:
            args[-1] = ':' + str(args[-1])
        self.send(':%s %s %s %s' % (self.s.name, code, self.nick or '*', ' '.join(str(a) for a in args)))


class Chan:
    def __init__(self, name):
        self.name = name
        self.members = {}
        self.modes = {'n': True, 't': True}
        self.bans = []
        self.invites = set()
        self.topic = ''


class IRCServer:
    def __init__(self, mode='plain', name='irc.test', on_line=None):
        self.mode = 'plain' if mode == 'other' else mode
        self.on_line = on_line            # on_line(nick, line): everything the server receives, as it comes
        self.name = name
        self.network = {'quakenet': 'QuakeNet', 'libera': 'Libera.Chat'}.get(mode, 'TestNet')
        self.svchost = 'services.libera.chat'
        self.banned_hosts = set()          # addresses the network bans (a K-line)
        self.clients = {}
        self.channels = {}
        self.accounts = {}       # lower account -> {'name', 'password', 'email'}
        self.chanlev = {}        # lower chan -> {lower account: flags}
        self.mail = []           # (email, account, password)
        self.received = []
        self.server = None
        self.port = None
        self.qhost = 'CServe.quakenet.org'
        self.throttle = 0           # seconds between connections; 0 = off
        self.last_connect = 0

    # ---- test API ----
    def add_account(self, name, password):
        self.accounts[low(name)] = {'name': name, 'password': password, 'email': '', 'certs': [],
                                    'verified': True}

    def set_chanlev(self, chan, account, flags):
        self.chanlev.setdefault(low(chan), {})[low(account)] = flags

    def client(self, nick):
        return self.clients.get(low(nick))

    def chan(self, name):
        return self.channels.get(low(name))

    def is_op(self, chan, nick):
        c = self.chan(chan)
        return bool(c) and '@' in c.members.get(low(nick), set())

    async def start(self, port=0, tls=None, host='127.0.0.1'):
        """Listen on host:port (port 0: a free one, then in .port). tls = (server cert file, server key file,
        [client certificates to accept]): a second, TLS port in .tls_port."""
        ctx = None
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(tls[0], tls[1])
            if tls[2]:
                ctx.verify_mode = ssl.CERT_OPTIONAL
                for f in tls[2]:
                    ctx.load_verify_locations(cafile=f)
        self.server = await asyncio.start_server(self.handle, host, port)
        self.port = self.server.sockets[0].getsockname()[1]
        self.tls_server, self.tls_port = None, None
        if ctx:
            self.tls_server = await asyncio.start_server(self.handle, host, 0, ssl=ctx)
            self.tls_port = self.tls_server.sockets[0].getsockname()[1]
        return self

    async def stop(self):
        for c in list(self.clients.values()):
            try:
                c.writer.close()
            except Exception:
                pass
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if getattr(self, 'tls_server', None):
            self.tls_server.close()
            await self.tls_server.wait_closed()

    def kill(self, nick, reason='Killed'):
        c = self.client(nick)
        if c:
            self.quit(c, reason)
            c.writer.close()

    # ---- connection ----
    async def handle(self, reader, writer):
        c = Client(self, reader, writer)
        so = writer.get_extra_info('ssl_object')
        peer = so.getpeercert(binary_form=True) if so else None
        c.certfp = hashlib.sha256(peer).hexdigest() if peer else ''
        if self.throttle:
            # like QuakeNet: connections from one host too close together are refused
            now = time.time()
            too_fast = now - self.last_connect < self.throttle
            self.last_connect = now
            if too_fast:
                writer.write(b'ERROR :Your host is trying to (re)connect too fast -- throttled\r\n')
                await writer.drain()
                writer.close()
                return
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.decode('utf-8', 'replace').rstrip('\r\n')
                if not line:
                    continue
                self.received.append((c.nick, line))
                if self.on_line:
                    self.on_line(c.nick, line)
                try:
                    self.dispatch(c, line)
                except Exception:
                    import traceback
                    traceback.print_exc()
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            if c.registered and low(c.nick) in self.clients and self.clients[low(c.nick)] is c:
                self.quit(c, 'Connection closed')
            try:
                writer.close()
            except Exception:
                pass

    def quit(self, c, reason):
        seen = set()
        for ch in list(self.channels.values()):
            if low(c.nick) in ch.members:
                del ch.members[low(c.nick)]
                for m in ch.members:
                    if m not in seen:
                        seen.add(m)
                        self.clients[m].send(':%s QUIT :%s' % (c.mask, reason))
                if not ch.members:
                    del self.channels[low(ch.name)]
        self.clients.pop(low(c.nick), None)

    @staticmethod
    def parse(line):
        if ' :' in line:
            head, trail = line.split(' :', 1)
            parts = head.split() + [trail]
        else:
            parts = line.split()
        return parts[0].upper(), parts[1:]

    def dispatch(self, c, line):
        cmd, p = self.parse(line)
        h = getattr(self, 'c_' + cmd, None)
        if not c.registered and cmd not in ('CAP', 'NICK', 'USER', 'PING', 'QUIT', 'AUTHENTICATE'):
            return
        if h:
            h(c, p)
        elif c.registered:
            c.num('421', cmd, 'Unknown command')

    def try_register(self, c):
        if c.registered or not c.nick or not c.user or c.cap_negotiating:
            return
        c.registered = True
        c.host = '%s.host' % c.user.lstrip('~').lower()
        if c.host in self.banned_hosts:                    # a K-line
            c.send('ERROR :Closing Link: %s (*** Banned )' % c.host)
            c.registered = False
            c.writer.close()
            return
        if self.mode == 'quakenet':
            c.modes.add('i')        # invisible by default, as on QuakeNet
        self.clients[low(c.nick)] = c
        c.num('001', 'Welcome to the test network %s' % c.mask)
        c.num('002', 'Your host is %s' % self.name)
        c.num('003', 'This server was created today')
        c.num('004', self.name, 'fake', 'iowx', 'biklmnopstv')
        tokens = ['PREFIX=(ov)@+', 'CHANMODES=b,k,l,imnpst', 'CHANTYPES=#&', 'NICKLEN=15',
                  'CASEMAPPING=rfc1459', 'NETWORK=%s' % self.network, 'WHOX']
        c.num('005', *(tokens + ['are supported by this server']))
        c.num('375', '- %s Message of the day -' % self.name)
        c.num('372', '- Test server')
        c.num('376', 'End of /MOTD command.')

    # ---- registration ----
    def c_CAP(self, c, p):
        if self.mode == 'quakenet':
            c.num('421', 'CAP', 'Unknown command')
            return
        sub = p[0].upper() if p else ''
        offered = ['account-notify', 'extended-join', 'account-tag', 'multi-prefix', 'away-notify'] + \
            (['sasl'] if self.mode == 'libera' else [])
        if sub == 'LS':
            c.cap_negotiating = not c.registered
            c.send(':%s CAP %s LS :%s' % (self.name, c.nick or '*', ' '.join(offered)))
        elif sub == 'REQ':
            want = p[-1].split()
            if all(w in offered for w in want):
                c.caps.update(want)
                c.send(':%s CAP %s ACK :%s' % (self.name, c.nick or '*', ' '.join(want)))
            else:
                c.send(':%s CAP %s NAK :%s' % (self.name, c.nick or '*', ' '.join(want)))
        elif sub == 'END':
            c.cap_negotiating = False
            self.try_register(c)

    def c_NICK(self, c, p):
        new = p[0] if p else ''
        if not re.match(r'^[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,14}$', new):
            c.num('432', new, 'Erroneous nickname')
            return
        other = self.clients.get(low(new))
        if other and other is not c:
            c.num('433', new, 'Nickname is already in use')
            return
        if not c.registered:
            c.nick = new
            self.try_register(c)
            return
        old = c.mask
        del self.clients[low(c.nick)]
        targets = {low(c.nick)}
        for ch in self.channels.values():
            if low(c.nick) in ch.members:
                ch.members[low(new)] = ch.members.pop(low(c.nick))
                targets.update(ch.members)
        c.nick = new
        self.clients[low(new)] = c
        targets.discard(low(old.split('!')[0]))
        targets.add(low(new))
        for t in targets:
            if t in self.clients:
                self.clients[t].send(':%s NICK :%s' % (old, new))

    def c_USER(self, c, p):
        if len(p) >= 4 and not c.registered:
            c.user = p[0][:10]
            c.real = p[3]
            self.try_register(c)

    def c_PING(self, c, p):
        c.send(':%s PONG %s :%s' % (self.name, self.name, p[-1] if p else ''))

    def c_PONG(self, c, p):
        pass

    def c_QUIT(self, c, p):
        if c.registered:
            self.quit(c, 'Quit: ' + (p[-1] if p else ''))
        c.writer.close()

    # ---- channels ----
    def to_chan(self, ch, line, skip=None, cap=None):
        for m in ch.members:
            cl = self.clients.get(m)
            if cl and cl is not skip and (cap is None or cap in cl.caps):
                cl.send(line)

    def c_JOIN(self, c, p):
        if not p:
            return
        keys = p[1].split(',') if len(p) > 1 else []
        for i, name in enumerate(p[0].split(',')):
            if not name.startswith(('#', '&')):
                c.num('403', name, 'No such channel')
                continue
            ch = self.chan(name)
            new = ch is None
            if new:
                ch = self.channels[low(name)] = Chan(name)
            if low(c.nick) in ch.members:
                continue
            invited = low(c.nick) in ch.invites
            if not new:
                if any(wild(b, c.mask) for b in ch.bans) and not invited:
                    c.num('474', ch.name, 'Cannot join channel (+b)')
                    continue
                if ch.modes.get('r') and not c.account:
                    c.num('477', ch.name, 'Cannot join channel (+r) - you need to be logged into your NickServ account')
                    continue
                if 'i' in ch.modes and not invited:
                    c.num('473', ch.name, 'Cannot join channel (+i)')
                    continue
                if 'k' in ch.modes and (keys[i] if i < len(keys) else '') != ch.modes['k'] and not invited:
                    c.num('475', ch.name, 'Cannot join channel (+k)')
                    continue
                if 'l' in ch.modes and len(ch.members) >= int(ch.modes['l']) and not invited:
                    c.num('471', ch.name, 'Cannot join channel (+l)')
                    continue
            ch.invites.discard(low(c.nick))
            ch.members[low(c.nick)] = {'@'} if new else set()
            for m in ch.members:
                cl = self.clients[m]
                if 'extended-join' in cl.caps:
                    cl.send(':%s JOIN %s %s :%s' % (c.mask, ch.name, c.account or '*', c.real))
                else:
                    cl.send(':%s JOIN %s' % (c.mask, ch.name))
            if ch.topic:
                c.num('332', ch.name, ch.topic)
            self.names(c, ch)

    def names(self, c, ch):
        multi = 'multi-prefix' in c.caps
        items = []
        inside = low(c.nick) in ch.members
        for m, pre in ch.members.items():
            cl = self.clients[m]
            if not inside and 'i' in cl.modes:
                continue                # invisible users are hidden from outsiders
            p = ('@' if '@' in pre else '') + ('+' if '+' in pre else '')
            items.append((p if multi else p[:1]) + cl.nick)
        c.num('353', '=', ch.name, ' '.join(items))
        c.num('366', ch.name, 'End of /NAMES list.')

    def c_NAMES(self, c, p):
        ch = self.chan(p[0]) if p else None
        if ch and ('s' not in ch.modes or low(c.nick) in ch.members):
            self.names(c, ch)
        else:
            c.num('366', p[0] if p else '*', 'End of /NAMES list.')

    def c_PART(self, c, p):
        for name in p[0].split(','):
            ch = self.chan(name)
            if not ch or low(c.nick) not in ch.members:
                continue
            self.to_chan(ch, ':%s PART %s :%s' % (c.mask, ch.name, p[1] if len(p) > 1 else ''))
            del ch.members[low(c.nick)]
            if not ch.members:
                del self.channels[low(ch.name)]

    def c_KICK(self, c, p):
        ch = self.chan(p[0])
        if not ch or not self.is_op(p[0], c.nick):
            c.num('482', p[0], "You're not channel operator")
            return
        victim = low(p[1])
        if victim in ch.members:
            self.to_chan(ch, ':%s KICK %s %s :%s' % (c.mask, ch.name, self.clients[victim].nick,
                                                      p[2] if len(p) > 2 else c.nick))
            del ch.members[victim]

    def c_INVITE(self, c, p):
        nick, name = p[0], p[1]
        ch = self.chan(name)
        t = self.client(nick)
        if not t:
            c.num('401', nick, 'No such nick')
            return
        if ch and not self.is_op(name, c.nick):
            c.num('482', name, "You're not channel operator")
            return
        if ch:
            ch.invites.add(low(nick))
        c.num('341', nick, name)
        t.send(':%s INVITE %s :%s' % (c.mask, t.nick, name))

    def c_TOPIC(self, c, p):
        ch = self.chan(p[0])
        if ch and len(p) > 1:
            ch.topic = p[1]
            self.to_chan(ch, ':%s TOPIC %s :%s' % (c.mask, ch.name, p[1]))

    def c_MODE(self, c, p):
        target = p[0]
        if not target.startswith(('#', '&')):
            if low(target) == low(c.nick) and len(p) > 1:
                if p[1] == '+x' and c.account and self.mode == 'quakenet':
                    c.modes.add('x')
                    c.host = '%s.users.quakenet.org' % c.account
                    c.send(':%s MODE %s :+x' % (c.nick, c.nick))
                    c.num('396', c.host, 'is now your hidden host')
            return
        ch = self.chan(target)
        if not ch:
            c.num('403', target, 'No such channel')
            return
        if len(p) == 1:
            modes = ''.join(m for m in ch.modes if m not in 'kl')
            args = []
            if 'k' in ch.modes:
                modes += 'k'
                args.append(ch.modes['k'])
            if 'l' in ch.modes:
                modes += 'l'
                args.append(str(ch.modes['l']))
            c.num('324', ch.name, '+' + modes, *args) if args else c.num('324', ch.name, '+' + modes)
            return
        if p[1] in ('b', '+b') and len(p) == 2:
            for b in ch.bans:
                c.num('367', ch.name, b, 'someone', '0')
            c.num('368', ch.name, 'End of Channel Ban List')
            return
        self.apply_modes(c.mask, ch, p[1], p[2:], check=c)

    def apply_modes(self, by, ch, modestr, args, check=None):
        if check is not None and not self.is_op(ch.name, check.nick):
            check.num('482', ch.name, "You're not channel operator")
            return
        sign, args, done, dargs = '+', list(args), [], []
        for m in modestr:
            if m in '+-':
                sign = m
                continue
            if m in 'ov':
                if not args:
                    continue
                a = args.pop(0)
                pre = ch.members.get(low(a))
                if pre is None:
                    continue
                ch_ = '@' if m == 'o' else '+'
                (pre.add if sign == '+' else pre.discard)(ch_)
                done.append(sign + m)
                dargs.append(self.clients[low(a)].nick)
            elif m == 'b':
                if not args:
                    continue
                a = args.pop(0)
                if sign == '+' and a not in ch.bans:
                    ch.bans.append(a)
                elif sign == '-' and a in ch.bans:
                    ch.bans.remove(a)
                else:
                    continue
                done.append(sign + m)
                dargs.append(a)
            elif m in 'kl':
                if sign == '+':
                    if not args:
                        continue
                    a = args.pop(0)
                    ch.modes[m] = a
                    dargs.append(a)
                else:
                    ch.modes.pop(m, None)
                    if m == 'k' and args:
                        dargs.append(args.pop(0))
                done.append(sign + m)
            else:
                if sign == '+':
                    ch.modes[m] = True
                else:
                    ch.modes.pop(m, None)
                done.append(sign + m)
        if done:
            out, last = '', ''
            for d in done:
                if d[0] != last:
                    out += d[0]
                    last = d[0]
                out += d[1]
            self.to_chan(ch, ':%s MODE %s %s' % (by, ch.name, ' '.join([out] + dargs)))

    # ---- queries ----
    def c_WHO(self, c, p):
        target = p[0] if p else '*'
        whox = len(p) > 1 and p[1].startswith('%')
        fields, token = '', ''
        if whox:
            spec = p[1][1:]
            fields, _, token = spec.partition(',')
        rows = []
        ch = self.chan(target)
        if ch:
            if 's' in ch.modes and low(c.nick) not in ch.members:
                rows = []
            else:
                inside = low(c.nick) in ch.members
                rows = [(ch.name, self.clients[m], ch.members[m]) for m in ch.members
                        if inside or 'i' not in self.clients[m].modes]
        else:
            t = self.client(target)
            if t:
                rows = [('*', t, set())]
        for chname, cl, pre in rows:
            flags = 'H' + ('*' if 'o' in cl.modes else '') + ('@' if '@' in pre else ('+' if '+' in pre else ''))
            if whox:
                out = []
                for f in 'tcuihsnfdlaor':
                    if f not in fields:
                        continue
                    out.append({'t': token or '0', 'c': chname, 'u': cl.user, 'i': '255.255.255.255',
                                'h': cl.host, 's': self.name, 'n': cl.nick, 'f': flags, 'd': '0', 'l': '0',
                                'a': cl.account or '0', 'o': 'n/a', 'r': ':' + cl.real}[f])
                c.send(':%s 354 %s %s' % (self.name, c.nick, ' '.join(out)))
            else:
                c.num('352', chname, cl.user, cl.host, self.name, cl.nick, flags, '0 ' + cl.real)
        c.num('315', target, 'End of /WHO list.')

    def c_WHOIS(self, c, p):
        t = self.client(p[-1])
        if not t:
            c.num('401', p[-1], 'No such nick')
            c.num('318', p[-1], 'End of /WHOIS list.')
            return
        c.num('311', t.nick, t.user, t.host, '*', t.real)
        if t.account:
            c.num('330', t.nick, t.account, 'is authed as')
        c.num('318', t.nick, 'End of /WHOIS list.')

    # ---- messages ----
    def c_PRIVMSG(self, c, p, kind='PRIVMSG'):
        if len(p) < 2:
            return
        target, text = p[0], p[1]
        if self.mode == 'quakenet' and low(target) in ('q', 'q@cserve.quakenet.org'):
            self.q(c, text)
            return
        if self.mode == 'libera' and low(target) in ('nickserv', 'chanserv'):
            (self.ns if low(target) == 'nickserv' else self.cs)(c, text)
            return
        ch = self.chan(target)
        if ch:
            for m in ch.members:
                cl = self.clients[m]
                if cl is c:
                    continue
                tag = '@account=%s ' % c.account if c.account and 'account-tag' in cl.caps else ''
                cl.send('%s:%s %s %s :%s' % (tag, c.mask, kind, ch.name, text))
            return
        t = self.client(target)
        if t:
            tag = '@account=%s ' % c.account if c.account and 'account-tag' in t.caps else ''
            t.send('%s:%s %s %s :%s' % (tag, c.mask, kind, t.nick, text))
        elif kind == 'PRIVMSG':
            c.num('401', target, 'No such nick')

    def c_NOTICE(self, c, p):
        self.c_PRIVMSG(c, p, 'NOTICE')

    def c_ISON(self, c, p):
        online = [self.clients[low(n)].nick for n in ' '.join(p).split() if low(n) in self.clients]
        c.num('303', ' '.join(online))

    # ---- SASL, NickServ and ChanServ (mode='libera') ----
    def c_AUTHENTICATE(self, c, p):
        arg = p[0] if p else ''
        if arg.upper() in ('PLAIN', 'EXTERNAL') and not c.sasl:
            c.sasl = arg.upper()
            c.send('AUTHENTICATE +')
            return
        acct = None
        if c.sasl == 'PLAIN' and arg != '+':
            try:
                _, authc, pw = base64.b64decode(arg).decode().split('\0')
                a = self.accounts.get(low(authc))
                acct = a if a and a['password'] == pw else None
            except Exception:
                acct = None
        elif c.sasl == 'EXTERNAL':
            acct = next((a for a in self.accounts.values() if c.certfp and c.certfp in a.get('certs', [])), None)
        c.sasl = None
        if acct:
            c.account = acct['name']
            c.num('900', '%s!%s@%s' % (c.nick or '*', c.user or '*', c.host or '*'), acct['name'],
                  'You are now logged in as %s' % acct['name'])
            c.num('903', 'SASL authentication successful')
        else:
            c.num('904', 'SASL authentication failed')

    def svc(self, c, who, text):
        c.send(':%s!%s@%s NOTICE %s :%s' % (who, who, self.svchost, c.nick, text))

    def set_account(self, c, name):
        c.account = name
        seen = set()
        for ch in self.channels.values():
            if low(c.nick) in ch.members:
                for m in ch.members:
                    cl = self.clients.get(m)
                    if cl and cl is not c and m not in seen and 'account-notify' in cl.caps:
                        seen.add(m)
                        cl.send(':%s ACCOUNT %s' % (c.mask, name or '*'))

    def ns(self, c, text):
        parts = text.split()
        cmd = parts[0].upper() if parts else ''
        if cmd == 'IDENTIFY' and len(parts) >= 2:
            name, pw = (parts[1], parts[2]) if len(parts) >= 3 else (c.nick, parts[1])
            a = self.accounts.get(low(name))
            if a and a['password'] == pw:
                self.set_account(c, a['name'])
                self.svc(c, 'NickServ', 'You are now identified for \x02%s\x02.' % a['name'])
            else:
                self.svc(c, 'NickServ', 'Invalid password for \x02%s\x02.' % name)
        elif cmd == 'REGISTER' and len(parts) >= 3:
            if low(c.nick) in self.accounts:
                self.svc(c, 'NickServ', '\x02%s\x02 is already registered.' % c.nick)
                return
            code = secrets.token_hex(6)
            self.accounts[low(c.nick)] = {'name': c.nick, 'password': parts[1], 'email': parts[2], 'certs': [],
                                          'verified': False, 'code': code}
            self.mail.append((parts[2], c.nick, code))
            self.set_account(c, c.nick)
            self.svc(c, 'NickServ', 'An email containing nickname activation instructions has been sent to '
                                    '\x02%s\x02.' % parts[2])
        elif cmd == 'VERIFY' and len(parts) >= 4:
            a = self.accounts.get(low(parts[2]))
            if a and a.get('code') == parts[3]:
                a['verified'] = True
                self.svc(c, 'NickServ', '\x02%s\x02 has now been verified.' % a['name'])
            else:
                self.svc(c, 'NickServ', 'Verification failed. Invalid key for \x02%s\x02.' % parts[2])
        elif cmd == 'CERT' and len(parts) >= 2 and parts[1].upper() == 'ADD':
            a = self.accounts.get(low(c.account)) if c.account else None
            if not a:
                self.svc(c, 'NickServ', 'You are not logged in.')
            elif not c.certfp:
                self.svc(c, 'NickServ', 'You are not using a client certificate.')
            elif c.certfp in a['certs']:
                self.svc(c, 'NickServ', 'Fingerprint \x02%s\x02 is already on your fingerprint list.' % c.certfp)
            else:
                a['certs'].append(c.certfp)
                self.svc(c, 'NickServ', 'Added fingerprint \x02%s\x02 to your fingerprint list.' % c.certfp)
        elif cmd == 'REGAIN' and len(parts) >= 2:
            target = self.accounts.get(low(parts[1]))
            if not c.account or not target or low(target['name']) != low(c.account):
                self.svc(c, 'NickServ', 'You are not authorized to regain \x02%s\x02.' % parts[1])
                return
            holder = self.client(parts[1])
            if holder and holder is not c:
                self.c_NICK(holder, ['Guest%d' % secrets.randbelow(90000)])
            self.c_NICK(c, [parts[1]])
            self.svc(c, 'NickServ', '\x02%s\x02 has been regained.' % parts[1])
        else:
            self.svc(c, 'NickServ', 'Invalid command.')

    def cs(self, c, text):
        parts = text.split()
        cmd = parts[0].upper() if parts else ''
        if cmd not in ('OP', 'INVITE', 'UNBAN') or len(parts) < 2:
            self.svc(c, 'ChanServ', 'Invalid command.')
            return
        name = parts[1]
        ch = self.chan(name)
        flags = self.chanlev.get(low(name), {}).get(low(c.account), '') if c.account else ''
        if not ch:
            self.svc(c, 'ChanServ', '\x02%s\x02 is not registered.' % name)
            return
        if not flags:
            self.svc(c, 'ChanServ', 'You are not authorized to perform this operation.')
            return
        who = 'ChanServ!ChanServ@%s' % self.svchost
        if cmd == 'OP':
            if low(c.nick) in ch.members and '@' not in ch.members[low(c.nick)]:
                self.apply_modes(who, ch, '+o', [c.nick])
        elif cmd == 'INVITE':
            ch.invites.add(low(c.nick))
            c.send(':%s INVITE %s :%s' % (who, c.nick, ch.name))
            self.svc(c, 'ChanServ', 'You have been invited to \x02%s\x02.' % ch.name)
        else:
            for b in [b for b in ch.bans if wild(b, c.mask)]:
                self.apply_modes(who, ch, '-b', [b])
            self.svc(c, 'ChanServ', 'You have been unbanned from \x02%s\x02.' % ch.name)

    # ---- Q ----
    def qsay(self, c, text):
        c.send(':Q!TheQBot@%s NOTICE %s :%s' % (self.qhost, c.nick, text))

    def q(self, c, text):
        parts = text.split()
        if not parts:
            return
        cmd = parts[0].upper()
        if cmd == 'CHALLENGE':
            c.challenge = secrets.token_hex(16)
            self.qsay(c, 'CHALLENGE %s HMAC-MD5 HMAC-SHA-1 HMAC-SHA-256 LEGACY-MD5' % c.challenge)
        elif cmd == 'CHALLENGEAUTH' and len(parts) >= 4:
            acct = self.accounts.get(low(parts[1]))
            ok = acct and c.challenge and parts[3] == 'HMAC-SHA-256' and \
                parts[2] == q_expected(acct['name'], acct['password'], c.challenge)
            c.challenge = None
            if ok:
                c.account = acct['name']
                self.qsay(c, 'You are now logged in as %s.' % acct['name'])
            else:
                self.qsay(c, 'Username or password incorrect.')
        elif cmd == 'HELLO' and len(parts) >= 3:
            if c.account:
                self.qsay(c, 'HELLO is not available once you have authed.')
            elif low(c.nick) in self.accounts:
                self.qsay(c, 'Someone already has the account name %s!' % c.nick)
            elif parts[1] != parts[2] or '@' not in parts[1]:
                self.qsay(c, 'Your email addresses are invalid or do not match.')
            else:
                pw = secrets.token_hex(5)
                self.accounts[low(c.nick)] = {'name': c.nick, 'password': pw, 'email': parts[1]}
                self.mail.append((parts[1], c.nick, pw))
                self.qsay(c, 'Hello %s! Account %s created successfully. Your password has been sent to %s.'
                          % (c.nick, c.nick, parts[1]))
        elif cmd in ('OP', 'INVITE', 'UNBANME') and len(parts) >= 2:
            name = parts[1]
            flags = self.chanlev.get(low(name), {}).get(low(c.account), '') if c.account else ''
            ch = self.chan(name)
            if not ch or not flags:
                self.qsay(c, 'You do not have sufficient access on %s to use %s.' % (name, cmd.lower()))
                return
            if cmd == 'OP':
                if low(c.nick) in ch.members and '@' not in ch.members[low(c.nick)]:
                    self.apply_modes('Q!TheQBot@%s' % self.qhost, ch, '+o', [c.nick])
            elif cmd == 'INVITE':
                ch.invites.add(low(c.nick))
                c.send(':Q!TheQBot@%s INVITE %s :%s' % (self.qhost, c.nick, ch.name))
            else:
                for b in [b for b in ch.bans if wild(b, c.mask)]:
                    self.apply_modes('Q!TheQBot@%s' % self.qhost, ch, '-b', [b])
            self.qsay(c, 'Done.')
        else:
            self.qsay(c, 'Unknown command %s. Type SHOWCOMMANDS for a list of available commands.' % cmd)


FakeIRCd = IRCServer          # the name it had in ghostbot's test lab

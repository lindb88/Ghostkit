"""botlink: link your own IRC bots into a botnet, a hub and leaves, as docs/botnet.md describes.

Optional: a bot works alone without it; give it a role (hub or leaf) and it links. Python 3.8+, standard
library only (making a certificate uses the openssl command). Written to be read and ported: every rule from
docs/botnet.md and docs/security.md points 5 and 6 is here, with a comment saying why.

The protocol (one JSON object per line, over TLS):

    leaf -> hub   {"t": "hello", "name": "Leaf1", "nonce": "<16-128 chars>", "role": "leaf"}
    hub  -> leaf  {"t": "challenge", "nonce": "<hub nonce>"}
    leaf -> hub   {"t": "auth", "mac": HMAC-SHA256(secret, "leaf:" + hub_nonce + ":" + leaf_name)}
    hub  -> leaf  {"t": "ok", "name": "Hub", "mac": HMAC-SHA256(secret, "hub:" + leaf_nonce + ":" + leaf_name)}
                  or {"t": "denied"} (a second later, the same for a wrong name and a wrong secret)
    then          {"t": "ping"} / {"t": "pong"}, {"t": "put", "entries": {...}} (shared data), and the bot's own
                  messages (status, orders from the hub), handed to on_message.

    hub = Hub('Hub', secret, 'link.crt', 'link.key', port=7000, on_message=...)
    await hub.start()
    leaf = Leaf('Leaf1', secret, '127.0.0.1:7000', fingerprint, on_message=...)
    asyncio.ensure_future(leaf.run())

Part of ghostkit (MIT license).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import ssl
import subprocess
import time

log = logging.getLogger('botlink')

LOGIN_LINE = 4096            # before the login: a stranger cannot make us hold a big line
LINE_MAX = 65536             # after it; a big shared table goes in pieces (chunks())
MAX_DEPTH = 100              # nesting in a message; far deeper than anything real
LOGIN_TIMEOUT = 15           # seconds to finish the login
PENDING_PER_IP, PENDING_MAX = 4, 64     # logins waiting at once, per address and in all
PING_EVERY, DEAD_AFTER = 30, 90         # heartbeats: a TCP connection can look open long after the other side died
RATE_BYTES, RATE_MSGS = 16 << 20, 3000  # per link and minute, after the login; more ends the link (a stolen bot)
FUTURE_SLACK = 600           # shared data: seconds a timestamp may be ahead of our clock
TOMBSTONE_DAYS = 90          # deletions are remembered this long, or an offline bot brings them back
NAME_RX = re.compile(r'[A-Za-z0-9_\-\[\]\\`^{}|]{1,32}$')     # an IRC-nick-like bot name


# ---------------------------------------------------------------------------------------------------------
# The login's proof, and messages
# ---------------------------------------------------------------------------------------------------------

def link_mac(secret, kind, nonce, name):
    """HMAC-SHA256(secret, kind + ":" + nonce + ":" + name), hex. kind: "leaf" or "hub"."""
    return hmac.new(secret.encode(), ('%s:%s:%s' % (kind, nonce, name)).encode(), hashlib.sha256).hexdigest()


def _no_constant(name):
    raise ValueError('%s is not a number a bot sends' % name)


def decode(line):
    """One message: a JSON object, or ValueError. Refuses NaN and Infinity (a NaN timestamp never compares as
    older, Infinity always wins) and nesting deeper than MAX_DEPTH, checked without recursion."""
    try:
        m = json.loads(line.decode('utf-8'), parse_constant=_no_constant)
    except UnicodeDecodeError:
        raise ValueError('not UTF-8')
    except RecursionError:
        raise ValueError('nested too deep')
    if not isinstance(m, dict):
        raise ValueError('not a JSON object')
    stack = [(m, 1)]
    while stack:
        v, d = stack.pop()
        if d > MAX_DEPTH:
            raise ValueError('nested deeper than %d' % MAX_DEPTH)
        for x in (v.values() if isinstance(v, dict) else v):
            if isinstance(x, (dict, list)):
                stack.append((x, d + 1))
    return m


def chunks(entries, size=32768):
    """Shared-data entries in pieces whose messages stay well under LINE_MAX."""
    part, used = {}, 0
    for k, e in entries.items():
        n = len(json.dumps({k: e})) + 2
        if part and used + n > size:
            yield part
            part, used = {}, 0
        part[k], used = e, used + n
    if part:
        yield part


def encode(msg):
    return (json.dumps(msg, separators=(',', ':'), allow_nan=False) + '\n').encode()


async def read_line(reader, limit):
    """A line of at most limit bytes, or ValueError; b'' when the other side closed."""
    try:
        line = await reader.readuntil(b'\n')
    except asyncio.IncompleteReadError:
        return b''                                   # closed (in the middle of a line, perhaps)
    except asyncio.LimitOverrunError:
        raise ValueError('line too long')
    if len(line) > limit:
        raise ValueError('line too long')
    return line


# ---------------------------------------------------------------------------------------------------------
# Certificates: the leaf pins the hub's, taken from the hub's own machine (docs/security.md point 6)
# ---------------------------------------------------------------------------------------------------------

def make_cert(certfile, keyfile, name='botlink hub', days=3650):
    """A self-made certificate and key (openssl). Self-made is fine: the pinned fingerprint is the proof."""
    os.makedirs(os.path.dirname(os.path.abspath(certfile)), exist_ok=True)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:prime256v1',
                    '-nodes', '-days', str(days), '-subj', '/CN=' + name, '-keyout', keyfile, '-out', certfile],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.chmod(keyfile, 0o600)


def fingerprint_der(der):
    return hashlib.sha256(der).hexdigest()


def cert_fingerprint(certfile):
    """The SHA-256 fingerprint of a PEM certificate file; the same as
    openssl x509 -in hub.crt -outform DER | sha256sum"""
    with open(certfile) as f:
        return fingerprint_der(ssl.PEM_cert_to_DER_cert(f.read()))


def norm_fp(fp):
    return re.sub(r'[^0-9a-f]', '', str(fp).lower())


# ---------------------------------------------------------------------------------------------------------
# Shared data: every entry has its own timestamp; the newest change of each entry wins (docs/botnet.md)
# ---------------------------------------------------------------------------------------------------------

class Shared:
    """A table every linked bot has a copy of. set() and delete() make a change to send (send it with
    {"t": "put", "entries": change}); merge() takes in what another bot sent and returns what changed."""

    def __init__(self, path=None):
        self.path, self.entries = path, {}           # key -> {"v": value or None (deleted), "ts": time}
        if path and os.path.exists(path):
            with open(path) as f:
                got = json.load(f)
            self.merge(got if isinstance(got, dict) else {})

    def get(self, key, default=None):
        e = self.entries.get(key)
        return default if e is None or e['v'] is None else e['v']

    def items(self):
        return {k: e['v'] for k, e in self.entries.items() if e['v'] is not None}

    def set(self, key, value):
        old = self.entries.get(key, {}).get('ts', 0)
        e = {'v': value, 'ts': max(time.time(), old + 0.001)}      # always newer than what it replaces
        self.entries[key] = e
        self._save()
        return {key: e}

    def delete(self, key):
        return self.set(key, None)                   # remembered as deleted, not forgotten

    def merge(self, entries):
        if not isinstance(entries, dict):
            raise ValueError('entries are not an object')
        changed, limit = {}, time.time() + FUTURE_SLACK
        for k, e in entries.items():
            ts = e.get('ts') if isinstance(e, dict) else None
            # a real number (a bool is an int in Python: refuse it), not negative, and not far ahead of our
            # clock: an entry dated next year would win over every change until then
            if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not 0 <= ts <= limit:
                continue
            cur = self.entries.get(k)
            if cur is None or ts > cur['ts'] or (ts == cur['ts'] and _order(e.get('v')) > _order(cur['v'])):
                # equal timestamps: the larger value wins, the same on every bot, so two hubs that both
                # changed an entry at the same moment still end up agreeing
                self.entries[k] = changed[k] = {'v': e.get('v'), 'ts': ts}
        if changed:
            self._save()
        return changed

    def prune(self):
        cut = time.time() - TOMBSTONE_DAYS * 86400
        for k in [k for k, e in self.entries.items() if e['v'] is None and e['ts'] < cut]:
            del self.entries[k]

    def _save(self):
        if self.path:
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.entries, f)
            os.replace(tmp, self.path)              # never half a file


def _order(v):
    return json.dumps(v, sort_keys=True)


# ---------------------------------------------------------------------------------------------------------
# A linked bot, seen from the other end
# ---------------------------------------------------------------------------------------------------------

class Link:
    def __init__(self, name, reader, writer):
        self.name, self.reader, self.writer = name, reader, writer
        self.heard = time.time()
        self.closed = False

    def send(self, msg):
        if not self.closed:
            try:
                self.writer.write(encode(msg))
            except Exception:
                self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.writer.close()
            except Exception:
                pass


async def _read_messages(link, handle, line_max):
    """Messages until the link ends. One that cannot be decoded ends the link; one handle() cannot use (a wrong
    type where a name or number belongs) is logged and skipped. Many of those in a row end it too."""
    bad = []
    window, n_bytes, n_msgs = time.time(), 0, 0
    while not link.closed:
        line = await read_line(link.reader, line_max)
        if not line:
            return
        link.heard = now = time.time()
        if now - window > 60:
            window, n_bytes, n_msgs = now, 0, 0
        n_bytes, n_msgs = n_bytes + len(line), n_msgs + 1
        if n_bytes > RATE_BYTES or n_msgs > RATE_MSGS:
            raise ValueError('more than %d MB or %d messages in a minute' % (RATE_BYTES >> 20, RATE_MSGS))
        m = decode(line)
        if m.get('t') == 'ping':
            link.send({'t': 'pong'})
            continue
        if m.get('t') == 'pong':
            continue
        try:
            r = handle(link, m)
            if asyncio.iscoroutine(r):
                await r
        except (TypeError, ValueError, KeyError, AttributeError, IndexError) as e:
            now = time.time()
            bad = [t for t in bad if now - t < 60] + [now]
            log.warning('%s sent a %r message I could not use: %s', link.name, str(m.get('t'))[:30], e)
            if len(bad) > 20:
                raise ValueError('too many messages I could not use')


async def _heartbeat(link):
    while not link.closed:
        await asyncio.sleep(min(PING_EVERY, DEAD_AFTER / 3))
        if time.time() - link.heard > DEAD_AFTER:
            log.info('%s: nothing heard for %d seconds; dropping the link', link.name, DEAD_AFTER)
            link.close()
            return
        link.send({'t': 'ping'})


# ---------------------------------------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------------------------------------

class Hub:
    """Listens for leaves. on_message(name, msg) gets each leaf's messages ("put" is taken care of: merged
    into self.shared and passed on to the other leaves; on_message sees it too, with only what changed).
    on_link(name) / on_unlink(name) when a leaf comes and goes. The name is always the one the leaf logged in
    with: never believe a name inside a message (a leaf reports only about itself)."""

    def __init__(self, name, secret, certfile, keyfile, host='0.0.0.0', port=7000, shared=None,
                 on_message=None, on_link=None, on_unlink=None):
        if not secret or len(secret) < 12:
            raise ValueError('the link secret needs 12 characters or more')
        self.name, self.secret = name, secret
        self.certfile, self.keyfile, self.host, self.port = certfile, keyfile, host, port
        self.shared = shared if shared is not None else Shared()
        self.on_message, self.on_link, self.on_unlink = on_message, on_link, on_unlink
        self.leaves = {}                             # name -> Link
        self.pending = {}                            # address -> logins waiting
        self.server = None

    async def start(self):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(self.certfile, self.keyfile)
        self.server = await asyncio.start_server(self._accept, self.host, self.port, ssl=ctx, limit=LINE_MAX + 1)
        log.info('hub %s: listening on port %d', self.name, self.port)

    def close(self):
        for link in list(self.leaves.values()):
            link.close()
        if self.server:
            self.server.close()

    def send(self, name, msg):
        link = self.leaves.get(name)
        if link:
            link.send(msg)
        return link is not None

    def broadcast(self, msg, but=None):
        for name, link in list(self.leaves.items()):
            if name != but:
                link.send(msg)

    def put(self, change):
        """Share a change the hub made itself (from self.shared.set / delete)."""
        if change:
            self.broadcast({'t': 'put', 'entries': change})

    async def _accept(self, reader, writer):
        ip = (writer.get_extra_info('peername') or ('?',))[0]
        # Few logins waiting at once: otherwise idle connections fill the port and real bots cannot get in.
        if self.pending.get(ip, 0) >= PENDING_PER_IP or sum(self.pending.values()) >= PENDING_MAX:
            writer.close()
            return
        self.pending[ip] = self.pending.get(ip, 0) + 1
        waiting = True
        link = Link('a bot from %s' % ip, reader, writer)
        try:
            name = await asyncio.wait_for(self._login(link), LOGIN_TIMEOUT)
            if not name:
                return
            self.pending[ip] -= 1
            waiting = False
            link.name = name
            old = self.leaves.get(name)
            self.leaves[name] = link
            if old:
                old.close()                          # a new link replaces the old one (see the finally below)
            log.info('hub: %s linked', name)
            for part in chunks(self.shared.entries):  # our data; the leaf sends its own
                link.send({'t': 'put', 'entries': part})
            if self.on_link:
                self.on_link(name)
            beat = asyncio.ensure_future(_heartbeat(link))
            try:
                await _read_messages(link, self._handle, LINE_MAX)
            finally:
                beat.cancel()
        except (ConnectionError, OSError, ValueError, asyncio.TimeoutError, ssl.SSLError) as e:
            log.info('hub: link to %s ended: %s', link.name, e)
        finally:
            if waiting:
                self.pending[ip] -= 1
            if not self.pending.get(ip):
                self.pending.pop(ip, None)
            link.close()
            # The replacement trap: when a leaf reconnects, its old link is closed and ends up here. Clean up
            # only if this link is still the current one, or the leaf looks linked but gets nothing.
            if self.leaves.get(link.name) is link:
                del self.leaves[link.name]
                log.info('hub: %s unlinked', link.name)
                if self.on_unlink:
                    self.on_unlink(link.name)

    async def _login(self, link):
        hello = decode(await read_line(link.reader, LOGIN_LINE) or b'null')
        name, lnonce = hello.get('name'), hello.get('nonce')
        if hello.get('t') != 'hello' or not isinstance(name, str) or not NAME_RX.match(name) or \
                not isinstance(lnonce, str) or not 16 <= len(lnonce) <= 128:
            raise ValueError('not a login')
        nonce = secrets.token_hex(16)                # fresh every time: a recorded login is useless
        link.send({'t': 'challenge', 'nonce': nonce})
        auth = decode(await read_line(link.reader, LOGIN_LINE) or b'null')
        mac = auth.get('mac')
        if auth.get('t') != 'auth' or not isinstance(mac, str) or name == self.name or \
                not hmac.compare_digest(mac.encode(), link_mac(self.secret, 'leaf', nonce, name).encode()):
            log.warning('hub: refused a login as %r (wrong name or secret)', name)
            await asyncio.sleep(1)                   # no fast guessing; and the same answer for any reason
            link.send({'t': 'denied'})
            return None
        link.send({'t': 'ok', 'name': self.name, 'mac': link_mac(self.secret, 'hub', lnonce, name)})
        return name

    def _handle(self, link, m):
        if m.get('t') == 'put':
            change = self.shared.merge(m.get('entries'))
            if change:
                self.broadcast({'t': 'put', 'entries': change}, but=link.name)
                m = dict(m, entries=change)
            else:
                return
        if self.on_message:
            return self.on_message(link.name, m)


# ---------------------------------------------------------------------------------------------------------
# A leaf
# ---------------------------------------------------------------------------------------------------------

class Leaf:
    """Links to the hub and stays linked. on_message(msg) gets the hub's messages ("put" is merged into
    self.shared first; on_message sees only what changed). on_link() / on_unlink(). A leaf talks only to its
    hub, so orders can only come from there.

    fingerprint: the hub certificate's SHA-256, or a function returning it (read each time, so a leaf can
    start before the hub has made its certificate)."""

    retry_first, retry_max = 5, 300

    def __init__(self, name, secret, hub, fingerprint, shared=None, on_message=None, on_link=None,
                 on_unlink=None):
        if not secret or len(secret) < 12:
            raise ValueError('the link secret needs 12 characters or more')
        self.name, self.secret = name, secret
        host, _, port = hub.rpartition(':')
        self.host, self.port = host.strip('[]') or '127.0.0.1', int(port)
        self.fingerprint = fingerprint
        self.shared = shared if shared is not None else Shared()
        self.on_message, self.on_link, self.on_unlink = on_message, on_link, on_unlink
        self.hub = None                              # the Link while linked
        self.hub_name = ''
        self.running = True

    @property
    def linked(self):
        return self.hub is not None and not self.hub.closed

    def send(self, msg):
        if self.linked:
            self.hub.send(msg)
            return True
        return False

    def put(self, change):
        """Share a change this bot made (from self.shared.set / delete); sent again at the next link anyway."""
        if change:
            self.send({'t': 'put', 'entries': change})

    def close(self):
        self.running = False
        if self.hub:
            self.hub.close()

    async def run(self):
        delay = self.retry_first
        while self.running:
            ok = await self.link_once()
            if not self.running:
                return
            delay = self.retry_first if ok else min(delay * 2, self.retry_max)
            # some randomness, so a restarted hub is not hit by every leaf at the same moment
            await asyncio.sleep(delay * random.uniform(0.8, 1.2))

    async def link_once(self):
        """One link, until it ends. True if it got as far as linking."""
        try:
            want = norm_fp(self.fingerprint() if callable(self.fingerprint) else self.fingerprint)
        except OSError as e:
            log.info('leaf %s: no hub fingerprint yet (%s)', self.name, e)
            return False
        if len(want) != 64:
            log.error('leaf %s: the hub fingerprint must be a SHA-256 (64 hex digits)', self.name)
            return False
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # no CA: the pinned fingerprint is checked below
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port, ssl=ctx, limit=LINE_MAX + 1), 20)
        except (OSError, asyncio.TimeoutError, ssl.SSLError) as e:
            log.info('leaf %s: cannot reach the hub: %s', self.name, e)
            return False
        link = Link('the hub', reader, writer)
        linked = False
        try:
            der = writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
            if not hmac.compare_digest(fingerprint_der(der), want):
                # never send anything to a hub we cannot prove, and never repin by ourselves
                log.error('leaf %s: the hub certificate does not match the pinned fingerprint; not linking',
                          self.name)
                return False
            lnonce = secrets.token_hex(16)
            link.send({'t': 'hello', 'name': self.name, 'nonce': lnonce, 'role': 'leaf'})
            ch = decode(await asyncio.wait_for(read_line(reader, LOGIN_LINE), LOGIN_TIMEOUT) or b'null')
            if ch.get('t') != 'challenge' or not isinstance(ch.get('nonce'), str):
                raise ValueError('the hub did not send a challenge')
            link.send({'t': 'auth', 'mac': link_mac(self.secret, 'leaf', ch['nonce'], self.name)})
            ok = decode(await asyncio.wait_for(read_line(reader, LOGIN_LINE), LOGIN_TIMEOUT) or b'null')
            if ok.get('t') == 'denied':
                log.error('leaf %s: the hub refused the login: is [link] secret the same on both?', self.name)
                return False
            # the hub proves the secret too, over our nonce: someone who only copied the certificate cannot
            if ok.get('t') != 'ok' or not isinstance(ok.get('mac'), str) or not hmac.compare_digest(
                    ok['mac'].encode(), link_mac(self.secret, 'hub', lnonce, self.name).encode()):
                log.error('leaf %s: the hub could not prove the secret; not linking', self.name)
                return False
            self.hub, self.hub_name, linked = link, str(ok.get('name', 'hub')), True
            link.name = self.hub_name
            log.info('leaf %s: linked to %s', self.name, self.hub_name)
            for part in chunks(self.shared.entries):  # what we have, merged by the hub
                link.send({'t': 'put', 'entries': part})
            if self.on_link:
                self.on_link()
            beat = asyncio.ensure_future(_heartbeat(link))
            try:
                await _read_messages(link, self._handle, LINE_MAX)
            finally:
                beat.cancel()
        except (ConnectionError, OSError, ValueError, asyncio.TimeoutError, ssl.SSLError) as e:
            log.info('leaf %s: link ended: %s', self.name, e)
        finally:
            link.close()
            if self.hub is link:
                self.hub = None
                if self.on_unlink:
                    self.on_unlink()
        return linked

    def _handle(self, link, m):
        if m.get('t') == 'put':
            change = self.shared.merge(m.get('entries'))
            if not change:
                return
            m = dict(m, entries=change)
        if self.on_message:
            return self.on_message(m)

"""irctools.safefetch: fetch an address someone posted on IRC without letting them reach your own machine.

A bot that fetches links people post (to show a page's title, read a feed, expand a short link) is asked to
connect wherever the link says. Without care, "!title http://127.0.0.1:8080/admin" or a link to the home
router makes the bot fetch from inside your own network and say what it found (SSRF). Checking the address
first is not enough: a name can answer a public address to the check and a private one a moment later, when
the HTTP library looks it up again to connect (DNS rebinding). This module connects to exactly the address it
checked, on every redirect too.

    from irctools import safefetch

    title = safefetch.page_title('https://example.org/article')      # 'Example Domain', or raises FetchError
    page = safefetch.fetch('https://example.org/', max_bytes=500_000)
    page.data, page.url, page.content_type, page.charset

Blocking: in an asyncio bot, run it on a thread:
    title = await asyncio.get_event_loop().run_in_executor(None, safefetch.page_title, url)

What it refuses: anything but http and https; this machine, private and local networks, carrier-grade NAT,
link-local (cloud metadata at 169.254.169.254), multicast and reserved addresses, also inside IPv6; redirects
to any of those, and from https to plain http; more than 5 redirects; more than max_bytes; slower than timeout.
It never uses a proxy from the environment (a proxy would connect for it, past the checks).
Part of ghostkit (MIT license). See docs/security.md.
"""

import functools
import html
import http.client
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

__all__ = ['fetch', 'page_title', 'is_public', 'FetchError', 'Page']


class FetchError(Exception):
    """The address may not be fetched, or fetching it failed; str() says why, in words for people."""


class Page:
    def __init__(self, data, url, content_type, charset):
        self.data, self.url, self.content_type, self.charset = data, url, content_type, charset

    @property
    def text(self):
        return self.data.decode(self.charset or 'utf-8', 'replace')


def is_public(address):
    """True when an IP address is out on the internet: not this machine, a private or local network, carrier-
    grade NAT (100.64.0.0/10), link-local, multicast or reserved. IPv4 written inside IPv6 counts as IPv4."""
    try:
        ip = ipaddress.ip_address(str(address).split('%')[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_loopback or ip.is_private or ip.is_link_local)


def _check_url(url, allow_private):
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        raise FetchError('not a web address')
    if u.scheme not in ('http', 'https') or not u.hostname:
        raise FetchError('only http and https addresses')
    if u.username or u.password:
        raise FetchError('no names or passwords in the address')
    try:
        u.port
    except ValueError:
        raise FetchError('not a valid port')
    return u


def _connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, allow_private=False):
    """Look the name up once, check every address, connect to exactly those: no second lookup to rebind."""
    host, port = address
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise OSError('%s: the name does not resolve' % host)
    if not allow_private:
        for info in infos:
            if not is_public(info[4][0]):
                raise OSError('%s: a private or local address' % host)
    err = None
    for family, stype, proto, _, sockaddr in infos:
        sock = socket.socket(family, stype, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as e:
            err = e
            sock.close()
    raise err or OSError('%s: no address' % host)


class _HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *a, allow_private=False, **kw):
        super().__init__(*a, **kw)
        self._create_connection = functools.partial(_connect, allow_private=allow_private)


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, allow_private=False, **kw):
        super().__init__(*a, **kw)
        self._create_connection = functools.partial(_connect, allow_private=allow_private)


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, allow_private):
        super().__init__()
        self.allow_private = allow_private

    def http_open(self, req):
        return self.do_open(functools.partial(_HTTPConnection, allow_private=self.allow_private), req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, allow_private):
        super().__init__(context=ssl.create_default_context())
        self.allow_private = allow_private

    def https_open(self, req):
        return self.do_open(functools.partial(_HTTPSConnection, allow_private=self.allow_private), req,
                            context=self._context)


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def __init__(self, allow_private):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        _check_url(newurl, self.allow_private)          # the connection itself checks the address again
        if req.full_url.lower().startswith('https:') and not newurl.lower().startswith('https:'):
            raise FetchError('a redirect from https to plain http')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, max_bytes=1000000, timeout=10, allow_private=False, agent='irctools-safefetch',
          accept='text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5'):
    """Fetch url and return a Page; raises FetchError when it may not or could not. allow_private=True only for
    addresses you chose yourself (a feed on your home network), never for ones people post."""
    _check_url(url, allow_private)
    req = urllib.request.Request(url, headers={'User-Agent': agent, 'Accept': accept})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _HTTPHandler(allow_private),
                                         _HTTPSHandler(allow_private), _Redirects(allow_private))
    try:
        with opener.open(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            final, ctype = r.geturl(), r.headers.get_content_type()
            charset = r.headers.get_content_charset()
    except FetchError:
        raise
    except urllib.error.HTTPError as e:
        raise FetchError('the server answered %d' % e.code)
    except urllib.error.URLError as e:
        reason = e.reason if isinstance(e.reason, (str, OSError)) else str(e.reason)
        raise FetchError(str(reason)[:200])
    except (OSError, http.client.HTTPException, ValueError) as e:
        raise FetchError(str(e)[:200] or e.__class__.__name__)
    if len(data) > max_bytes:
        raise FetchError('larger than %d bytes' % max_bytes)
    return Page(data, final, ctype, charset)


_TITLE = re.compile(rb'<title[^>]*>(.*?)</title\s*>', re.I | re.S)
_META_CHARSET = re.compile(rb'<meta[^>]+charset=["\']?([\w-]+)', re.I)


def page_title(url, max_len=200, **kw):
    """The page's <title>, as one clean line (no tags, entities decoded, no control characters, at most max_len),
    ready to say on IRC. Raises FetchError when there is none or the page may not be fetched."""
    page = fetch(url, max_bytes=kw.pop('max_bytes', 500000), **kw)
    if page.content_type not in ('text/html', 'application/xhtml+xml'):
        raise FetchError('not a web page (%s)' % page.content_type)
    m = _TITLE.search(page.data)
    if not m:
        raise FetchError('the page has no title')
    charset = page.charset
    if not charset:
        mc = _META_CHARSET.search(page.data[:4096])
        charset = mc.group(1).decode('ascii', 'replace') if mc else 'utf-8'
    try:
        text = m.group(1).decode(charset, 'replace')
    except LookupError:
        text = m.group(1).decode('utf-8', 'replace')
    text = html.unescape(re.sub(r'<[^>]{0,200}>', '', text))
    text = ' '.join(re.sub(r'[\x00-\x1f\x7f  ]', ' ', text).split())
    if not text:
        raise FetchError('the page has no title')
    return text if len(text) <= max_len else text[:max_len - 1].rstrip() + '…'

"""irctools.safefetch: no fetching from this machine or a private network, however the address is dressed up."""

import http.server
import os
import socket
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from irctools import safefetch                     # noqa: E402
from irctools.safefetch import FetchError          # noqa: E402

PAGES = {
    '/page': (200, 'text/html; charset=utf-8',
              b'<html><head><title>Caf\xc3\xa9 &amp; <b>news</b>\r\nPRIVMSG #x :hi</title></head></html>'),
    '/latin1': (200, 'text/html', b'<meta charset="iso-8859-1"><title>R\xe4ksm\xf6rg\xe5s</title>'),
    '/notitle': (200, 'text/html', b'<html><body>no title here</body></html>'),
    '/image': (200, 'image/png', b'\x89PNG...'),
    '/big': (200, 'text/html', b'<title>big</title>' + b'x' * 300000),
    '/missing': (404, 'text/html', b'gone'),
}


class Handler(http.server.BaseHTTPRequestHandler):
    hits = []                                            # every request that reached this (local) server

    def log_message(self, *a):
        pass

    def do_GET(self):
        Handler.hits.append(self.path)
        if self.path.startswith('/to/'):
            self.send_response(302)
            self.send_header('Location', self.path[4:].replace('PORT', str(self.server.server_address[1])))
            self.end_headers()
            return
        code, ctype, body = PAGES.get(self.path, (404, 'text/plain', b''))
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestSafeFetch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.base = 'http://127.0.0.1:%d' % cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.real = socket.getaddrinfo

    def tearDown(self):
        socket.getaddrinfo = self.real

    def test_is_public(self):
        for a in ('93.184.216.34', '2606:2800:220:1:248:1893:25c8:1946', '8.8.8.8'):
            self.assertTrue(safefetch.is_public(a), a)
        for a in ('127.0.0.1', '10.0.0.1', '172.16.5.4', '192.168.1.1', '100.64.0.1', '169.254.169.254', '0.0.0.0',
                  '::1', 'fe80::1', 'fc00::1', '::ffff:127.0.0.1', '::ffff:192.168.0.1', '224.0.0.1', '255.255.255.255',
                  '240.0.0.1', 'not an ip'):
            self.assertFalse(safefetch.is_public(a), a)

    def test_private_and_odd_addresses_are_refused(self):
        for url in (self.base + '/page', 'http://localhost:%d/page' % self.port, 'http://[::1]/',
                    'http://10.1.2.3/', 'http://169.254.169.254/latest/meta-data/', 'http://0x7f000001/',
                    'http://2130706433/', 'http://[::ffff:127.0.0.1]/', 'file:///etc/passwd', 'gopher://x/',
                    'ftp://example.org/', 'http://user:pw@93.184.216.34/', 'not a url', 'http://x:99999/'):
            with self.assertRaises(FetchError, msg=url):
                safefetch.fetch(url, timeout=3)

    def test_dns_rebinding(self):
        """A name that answers a public address the first time and this machine after that: the fetcher looks
        it up once and connects to what it checked, so the second answer is never used."""
        calls = []

        def fake(host, port, *a, **kw):
            if host == 'rebind.example':
                calls.append(1)
                ip = '93.184.216.34' if len(calls) == 1 else '127.0.0.1'
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port))]
            return self.real(host, port, *a, **kw)
        socket.getaddrinfo = fake
        Handler.hits.clear()
        with self.assertRaises(FetchError):               # the public address does not answer in the test lab
            safefetch.fetch('http://rebind.example:%d/page' % self.port, timeout=2)
        self.assertEqual(len(calls), 1)                   # one lookup: nothing to rebind
        self.assertEqual(Handler.hits, [])                # and nothing reached this machine

    def test_a_name_with_public_and_private_addresses(self):
        def fake(host, port, *a, **kw):
            if host == 'mixed.example':
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port)),
                        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))]
            return self.real(host, port, *a, **kw)
        socket.getaddrinfo = fake
        with self.assertRaises(FetchError):
            safefetch.fetch('http://mixed.example:%d/page' % self.port, timeout=3)

    def test_allowed_private_works_and_redirects_are_checked(self):
        page = safefetch.fetch(self.base + '/page', allow_private=True)
        self.assertEqual(page.content_type, 'text/html')
        self.assertIn('Café', page.text)
        r = safefetch._Redirects(False)
        import urllib.request
        with self.assertRaises(FetchError):
            r.redirect_request(urllib.request.Request('https://93.184.216.34/'), None, 302, 'Found', {},
                               'http://93.184.216.34/')                  # https -> http
        with self.assertRaises(FetchError):
            r.redirect_request(urllib.request.Request('https://93.184.216.34/'), None, 302, 'Found', {},
                               'file:///etc/passwd')

    def test_redirect_into_the_local_network(self):
        """A public page that redirects to this machine: the redirect's connection is checked and refused."""
        def fake(host, port, *a, **kw):
            if host == 'public.example':                  # stands for a site on the internet; served here
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', self.port))]
            return self.real(host, port, *a, **kw)
        orig, checked = safefetch.is_public, []

        def first_hop_is_public(a):                       # the first connection (public.example) counts as public
            checked.append(a)
            return True if len(checked) == 1 else orig(a)
        socket.getaddrinfo, safefetch.is_public = fake, first_hop_is_public
        Handler.hits.clear()
        try:
            with self.assertRaises(FetchError) as e:
                safefetch.fetch('http://public.example:%d/to/http://127.0.0.1:PORT/page' % self.port, timeout=3)
        finally:
            safefetch.is_public = orig
        self.assertIn('private or local', str(e.exception))
        self.assertEqual(Handler.hits, ['/to/http://127.0.0.1:PORT/page'])   # the redirect itself, not the page

    def test_limits_and_errors(self):
        with self.assertRaises(FetchError) as e:
            safefetch.fetch(self.base + '/big', max_bytes=100000, allow_private=True)
        self.assertIn('larger than', str(e.exception))
        with self.assertRaises(FetchError) as e:
            safefetch.fetch(self.base + '/missing', allow_private=True)
        self.assertIn('404', str(e.exception))

    def test_environment_proxy_is_ignored(self):
        old = os.environ.get('http_proxy')
        os.environ['http_proxy'] = self.base                # a proxy that would fetch anything for us
        try:
            with self.assertRaises(FetchError):
                safefetch.fetch('http://10.9.9.9/page', timeout=3)
        finally:
            if old is None:
                os.environ.pop('http_proxy', None)
            else:
                os.environ['http_proxy'] = old

    def test_page_title_is_one_clean_line(self):
        t = safefetch.page_title(self.base + '/page', allow_private=True)
        self.assertEqual(t, 'Café & news PRIVMSG #x :hi')       # no tags, no line break: one IRC line
        self.assertNotIn('\r', t)
        self.assertEqual(safefetch.page_title(self.base + '/latin1', allow_private=True), 'Räksmörgås')
        for path, why in (('/notitle', 'no title'), ('/image', 'not a web page')):
            with self.assertRaises(FetchError) as e:
                safefetch.page_title(self.base + path, allow_private=True)
            self.assertIn(why, str(e.exception))
        self.assertTrue(safefetch.page_title(self.base + '/page', allow_private=True, max_len=8).endswith('…'))


if __name__ == '__main__':
    unittest.main()

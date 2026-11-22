"""webstatus: the format check, the writer, the server, the example bots' status files, and the page in a browser
(where playwright is installed)."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from irclab import IRCServer, User, until            # noqa: E402
import webstatus                                     # noqa: E402
from webstatus.__main__ import demo_bots, make_server   # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def good():
    return {'format': 1, 'updated': int(time.time()), 'title': 'T',
            'bots': [{'name': 'B', 'connected': True, 'since': 1795000000,
                      'channels': [{'name': '#a', 'users': 3, 'op': True}]}]}


class TestFormat(unittest.TestCase):
    def test_good(self):
        self.assertEqual(webstatus.check(good()), [])
        now = int(time.time())
        self.assertEqual(webstatus.check({'format': 1, 'updated': now, 'bots': demo_bots(now)}), [])

    def test_problems(self):
        cases = [
            ([], 'JSON object'),
            ({'updated': 1, 'bots': {}}, '"bots" must be a list'),
            ({'bots': []}, '"updated" is missing'),
            ({'updated': int(time.time() * 1000), 'bots': []}, 'milliseconds'),
            ({'updated': 1, 'bots': [{'connected': True}]}, 'needs a "name"'),
            ({'updated': 1, 'bots': [{'name': 'x', 'connected': 'yes'}]}, '"connected": true or false'),
            ({'updated': 1, 'bots': [{'name': 'x', 'connected': True, 'channels': [{'name': '#a', 'users': '3'}]}]},
             'users must be a number'),
            ({'format': 2, 'updated': 1, 'bots': []}, '"format" must be 1'),
        ]
        for data, want in cases:
            got = webstatus.check(data)
            self.assertTrue(any(want in p for p in got), (data, got))

    def test_write_status_is_atomic_and_readable(self):
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, 'www', 'status.json')
            webstatus.write_status(fn, good()['bots'], title='Mine')
            with open(fn) as f:
                data = json.load(f)
            self.assertEqual(webstatus.check(data), [])
            self.assertEqual(data['title'], 'Mine')
            self.assertEqual(os.stat(fn).st_mode & 0o777, 0o644)
            self.assertEqual(os.listdir(os.path.dirname(fn)), ['status.json'])   # no temporary file left

    def test_tighter_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, 'status.json')
            webstatus.write_status(fn, good()['bots'], mode=0o640)
            self.assertEqual(os.stat(fn).st_mode & 0o777, 0o640)
            from irctools import Bot
            bot = Bot('B', '127.0.0.1')
            bot.status_file(os.path.join(d, 'bot.json'), mode=0o600, network='Test')
            bot.write_status()
            self.assertEqual(os.stat(os.path.join(d, 'bot.json')).st_mode & 0o777, 0o600)
            with open(os.path.join(d, 'bot.json')) as f:
                data = json.load(f)
            self.assertEqual(webstatus.check(data), [])
            self.assertNotIn('_mode', data['bots'][0])

    def test_check_command(self):
        with tempfile.TemporaryDirectory() as d:
            fn = os.path.join(d, 's.json')
            with open(fn, 'w') as f:
                json.dump(good(), f)
            r = subprocess.run([sys.executable, '-m', 'webstatus', 'check', fn], cwd=ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            with open(fn, 'w') as f:
                json.dump({'bots': 'no'}, f)
            r = subprocess.run([sys.executable, '-m', 'webstatus', 'check', fn], cwd=ROOT, capture_output=True,
                               text=True)
            self.assertEqual(r.returncode, 1)


class TestServer(unittest.TestCase):
    def test_serves_only_the_page_and_json(self):
        with tempfile.TemporaryDirectory() as d:
            webstatus.write_status(os.path.join(d, 'status.json'), good()['bots'])
            with open(os.path.join(d, 'secret.txt'), 'w') as f:
                f.write('no')
            srv = make_server(d, port=0)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            base = 'http://127.0.0.1:%d' % srv.server_address[1]
            try:
                with urllib.request.urlopen(base + '/') as r:
                    self.assertIn('webstatus', r.read().decode())
                with urllib.request.urlopen(base + '/status.json?t=1') as r:
                    data = json.loads(r.read())
                self.assertEqual(data['bots'][0]['name'], 'B')
                for bad in ('/secret.txt', '/../status.json', '/.status-x.json', '/sub/status.json', '/none.json'):
                    with self.assertRaises(urllib.error.HTTPError, msg=bad) as e:
                        urllib.request.urlopen(base + bad)
                    self.assertEqual(e.exception.code, 404, bad)
            finally:
                srv.shutdown()
                srv.server_close()


def bot_command(kind, port, status):
    ex = os.path.join(ROOT, 'examples')
    if kind == 'python':
        return [sys.executable, os.path.join(ex, 'echobot.py'), '--port', str(port), '--fast', '--status', status], ex
    if kind == 'node':
        return ['node', 'hellobot.js', '--port', str(port), '--status', status, '--every', '1'], os.path.join(ex, 'node')
    return ['go', 'run', 'hellobot.go', '-port', str(port), '-status', status], os.path.join(ex, 'go')


class TestExampleBots(unittest.TestCase):
    """Each example bot writes a status.json in the format, with the right counts."""

    def run_bot(self, kind, nick):
        async def go():
            s = await IRCServer('plain').start()
            d = tempfile.mkdtemp()
            status = os.path.join(d, 'status.json')
            cmd, cwd = bot_command(kind, s.port, status)
            p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            try:
                await until(lambda: s.chan('#test') and nick.lower() in s.chan('#test').members, 90, 'the bot in')
                alice, bob = User('Alice'), User('Bob')
                await alice.connect(s.port)
                await bob.connect(s.port)
                await alice.join('#test')
                await bob.join('#test')
                await asyncio.sleep(0.5)
                bob.part('#test')

                def counted():
                    try:
                        with open(status) as f:
                            data = json.load(f)
                    except (OSError, ValueError):
                        return None
                    chans = data['bots'][0].get('channels') or []
                    return data if chans and chans[0]['users'] == 2 else None
                # the Go bot writes on join and every 30 s: a NICK change of its own makes no write, so wait
                await until(lambda: counted(), 40, 'status.json with 2 people in #test')
                data = counted()
                self.assertEqual(webstatus.check(data), [], data)
                b = data['bots'][0]
                self.assertEqual((b['name'], b['connected']), (nick, True))
                self.assertEqual(b['channels'][0]['name'], '#test')
                self.assertTrue(b['channels'][0]['op'])           # the first in gets ops
                self.assertIn('since', b)
                self.assertFalse([f for f in os.listdir(d) if f != 'status.json'])
                alice.close()
                bob.close()
            finally:
                os.killpg(p.pid, 15)
                p.wait(10)
                await s.stop()
                shutil.rmtree(d, ignore_errors=True)
        asyncio.run(asyncio.wait_for(go(), 150))

    def test_python(self):
        self.run_bot('python', 'EchoBot')

    @unittest.skipUnless(shutil.which('node'), 'no node')
    def test_node(self):
        self.run_bot('node', 'HelloBot')

    @unittest.skipUnless(shutil.which('go'), 'no go')
    def test_go(self):
        self.run_bot('go', 'HelloBot')


@unittest.skipUnless(sync_playwright, 'no playwright')
class TestPage(unittest.TestCase):
    """The page in a real browser: it shows the bots, never runs HTML from the file, and warns when it is old."""

    def show(self, data):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'status.json'), 'w') as f:
            json.dump(data, f)
        srv = make_server(d, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                b = p.chromium.launch()
                pg = b.new_page(viewport={'width': 390, 'height': 800})
                errors = []
                pg.on('pageerror', lambda e: errors.append(str(e)))
                pg.on('dialog', lambda dlg: (errors.append('dialog: ' + dlg.message), dlg.dismiss()))
                pg.goto('http://127.0.0.1:%d/' % srv.server_address[1])
                pg.wait_for_function("document.getElementById('summary').textContent !== 'Loading…'")
                out = {'text': pg.inner_text('body'), 'errors': errors,
                       'scripts': pg.evaluate("document.querySelectorAll('script').length"),
                       'imgs': pg.evaluate("document.querySelectorAll('img').length"),
                       'hscroll': pg.evaluate('document.documentElement.scrollWidth > innerWidth')}
                b.close()
            return out
        finally:
            srv.shutdown()
            srv.server_close()
            shutil.rmtree(d, ignore_errors=True)

    def test_shows_bots(self):
        now = int(time.time())
        out = self.show({'format': 1, 'updated': now, 'title': 'My bots', 'bots': demo_bots(now)})
        self.assertEqual(out['errors'], [])
        for want in ('My bots', 'HelloBot', 'connected for 3 days 5 hours', '#python', '1290', 'not connected',
                     '3 bots (2 connected), 4 channels, 1351 people.'):
            self.assertIn(want, out['text'])
        self.assertNotIn('old: the bot may have stopped', out['text'])
        self.assertFalse(out['hscroll'])

    def test_html_in_the_file_stays_text(self):
        evil = '<img src=x onerror=alert(1)><script>alert(2)</script>'
        out = self.show({'format': 1, 'updated': int(time.time()), 'title': evil,
                         'bots': [{'name': evil, 'connected': True, 'note': evil,
                                   'channels': [{'name': '#' + evil, 'users': 1}]}]})
        self.assertEqual(out['errors'], [])
        self.assertEqual((out['scripts'], out['imgs']), (1, 0))   # only the page's own script
        self.assertIn('<img src=x', out['text'])

    def test_old_status_warns(self):
        out = self.show({'format': 1, 'updated': int(time.time()) - 3 * 3600, 'bots': []})
        self.assertIn('3 hours old', out['text'])
        self.assertIn('No bots', out['text'])


if __name__ == '__main__':
    unittest.main()

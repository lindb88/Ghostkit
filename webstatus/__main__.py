"""python3 -m webstatus: the status page from the command line.

  python3 -m webstatus serve [FOLDER] [--port 8080] [--host 127.0.0.1]
      serve the page with FOLDER/status.json (default: the current folder) at http://127.0.0.1:8080/
  python3 -m webstatus check FILE
      is FILE in the status format? Exit status 0 when it is
  python3 -m webstatus install FOLDER
      copy index.html into FOLDER (your web server's), next to where your bot writes status.json
  python3 -m webstatus demo [--port 8080]
      the page with made-up bots, to see what it looks like
"""

import argparse
import functools
import http.server
import json
import os
import shutil
import sys
import tempfile
import time

from . import PAGE, check, write_status, __version__


class Handler(http.server.SimpleHTTPRequestHandler):
    """Only the page and .json files, from one folder; nothing else is served, no folder listings."""

    def __init__(self, *a, folder=None, **kw):
        self.folder = folder
        super().__init__(*a, directory=folder, **kw)

    def log_message(self, fmt, *args):
        pass

    def send_head(self):
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        if path in ('/', '/index.html'):
            with open(PAGE, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            return _Bytes(data)
        name = path.lstrip('/')
        if '/' in name or not name.endswith('.json') or name.startswith('.'):
            self.send_error(404)
            return None
        fn = os.path.join(self.folder, name)
        try:
            with open(fn, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(404, 'No %s here yet: the bot writes it' % name)
            return None
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        return _Bytes(data)


class _Bytes:
    def __init__(self, data):
        self.data = data

    def read(self, *a):
        d, self.data = self.data, b''
        return d

    def close(self):
        pass


def make_server(folder, host='127.0.0.1', port=8080):
    return http.server.ThreadingHTTPServer((host, port), functools.partial(Handler, folder=os.path.abspath(folder)))


def demo_bots(now):
    return [
        {'name': 'HelloBot', 'connected': True, 'network': 'Libera.Chat', 'server': 'irc.libera.chat',
         'since': now - 3 * 86400 - 5 * 3600, 'started': now - 4 * 86400, 'version': '1.2',
         'channels': [{'name': '#mybots', 'users': 41, 'op': True}, {'name': '#python', 'users': 1290},
                      {'name': '#test', 'users': 3, 'op': True}]},
        {'name': 'QuoteBot', 'connected': True, 'network': 'QuakeNet', 'since': now - 7200, 'version': '0.9',
         'note': 'New: !quote search <word>', 'channels': [{'name': '#quotes', 'users': 17, 'op': True}]},
        {'name': 'TestBot', 'connected': False, 'network': 'Libera.Chat', 'started': now - 600,
         'note': 'Reconnecting: the server said we came back too fast.'},
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python3 -m webstatus', description='A status page for IRC bots.')
    sub = ap.add_subparsers(dest='cmd')
    sv = sub.add_parser('serve', help='serve the page and status.json')
    sv.add_argument('folder', nargs='?', default='.')
    sv.add_argument('--port', type=int, default=8080)
    sv.add_argument('--host', default='127.0.0.1')
    ck = sub.add_parser('check', help='check a status file')
    ck.add_argument('file')
    ins = sub.add_parser('install', help='copy index.html into a folder')
    ins.add_argument('folder')
    dm = sub.add_parser('demo', help='the page with made-up bots')
    dm.add_argument('--port', type=int, default=8080)
    a = ap.parse_args(argv)

    if a.cmd == 'check':
        try:
            with open(a.file, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print('%s: %s' % (a.file, e))
            return 1
        problems = check(data)
        for p in problems:
            print('%s: %s' % (a.file, p))
        if not problems:
            print('%s: fine (%d bots)' % (a.file, len(data['bots'])))
        return 1 if problems else 0
    if a.cmd == 'install':
        os.makedirs(a.folder, exist_ok=True)
        shutil.copy(PAGE, os.path.join(a.folder, 'index.html'))
        print('Copied index.html into %s; have your bot write status.json there.' % a.folder)
        return 0
    if a.cmd in ('serve', 'demo'):
        folder = a.folder if a.cmd == 'serve' else tempfile.mkdtemp(prefix='webstatus-demo-')
        if a.cmd == 'demo':
            write_status(os.path.join(folder, 'status.json'), demo_bots(int(time.time())), title='My bots')
        host = getattr(a, 'host', '127.0.0.1')
        srv = make_server(folder, host, a.port)
        print('webstatus %s: http://%s:%d/ (status.json from %s; Ctrl-C stops it)' % (
            __version__, host, srv.server_address[1], os.path.abspath(folder)), flush=True)
        if host not in ('127.0.0.1', 'localhost', '::1'):
            print('Listening beyond this machine: everyone who can reach it sees the page.', flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())

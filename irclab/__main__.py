"""python3 -m irclab: the IRC test lab from the command line.

  python3 -m irclab serve [--port 6667] [--host 127.0.0.1] [--mode plain|libera|quakenet]
                          [--account NAME:PASSWORD ...] [--quiet]
      a test server to point your bot (and your own IRC client) at; prints what it receives
  python3 -m irclab run FILE.scenario [FILE ...] [--verbose]
      run scenarios (see irclab.scenario); exit status 0 when all pass
"""

import argparse
import asyncio
import sys

from . import __version__
from .scenario import run_file
from .server import IRCServer


async def serve(args):
    def show(nick, line):
        print('%-12s %s' % (nick or '(new)', line), flush=True)
    s = IRCServer(args.mode, on_line=None if args.quiet else show)
    for a in args.account or []:
        name, _, pw = a.partition(':')
        s.add_account(name, pw)
    await s.start(args.port, host=args.host)
    print('irclab %s: a %s IRC server on %s:%d (Ctrl-C stops it)' % (__version__, s.mode, args.host, s.port),
          flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await s.stop()


def main(argv=None):
    p = argparse.ArgumentParser(prog='python3 -m irclab', description='An IRC server for testing bots.')
    sub = p.add_subparsers(dest='cmd')
    sv = sub.add_parser('serve', help='run a test server')
    sv.add_argument('--port', type=int, default=6667)
    sv.add_argument('--host', default='127.0.0.1')
    sv.add_argument('--mode', default='plain', choices=['plain', 'libera', 'quakenet'])
    sv.add_argument('--account', action='append', metavar='NAME:PASSWORD', help='a services account')
    sv.add_argument('--quiet', action='store_true', help='do not print what the server receives')
    rn = sub.add_parser('run', help='run scenario files')
    rn.add_argument('files', nargs='+')
    rn.add_argument('--verbose', action='store_true', help="keep the bot's output file")
    args = p.parse_args(argv)
    if args.cmd == 'serve':
        try:
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == 'run':
        ok = True
        for f in args.files:
            if len(args.files) > 1:
                print('# %s' % f)
            ok = run_file(f, args.verbose) and ok
        return 0 if ok else 1
    p.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())

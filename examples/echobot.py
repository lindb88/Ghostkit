"""A small bot on irctools: !echo, !hello, !ops, and a welcome on join.

    python3 examples/echobot.py --server irc.libera.chat --port 6697 --tls --channel '#mytest'
    python3 -m irclab run examples/echobot.scenario        # tests it against a local test server
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from irctools import Bot  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--server', default='127.0.0.1')
ap.add_argument('--port', type=int, default=6667)
ap.add_argument('--tls', action='store_true')
ap.add_argument('--nick', default='EchoBot')
ap.add_argument('--channel', action='append', default=[])
ap.add_argument('--fast', action='store_true', help='no flood pacing (only for a local test server)')
ap.add_argument('--status', help='write status.json for webstatus here (e.g. www/status.json)')
ap.add_argument('-v', '--verbose', action='store_true')
a = ap.parse_args()
logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format='%(asctime)s %(message)s')

bot = Bot(a.nick, a.server, a.port, tls=a.tls, channels=a.channel or ['#test'],
          interval=0.05 if a.fast else 1.5, burst=20 if a.fast else 4)
if a.status:
    bot.status_file(a.status, every=5 if a.fast else 30, version='1.0')


@bot.command('!echo')
async def echo(ctx):
    ctx.reply(ctx.args or 'Echo what?')


@bot.command('!hello')
async def hello(ctx):
    ctx.reply('Hello, %s!' % ctx.nick)


@bot.command('!ops')
async def ops(ctx):
    if not ctx.where:
        ctx.tell('Ask in a channel.')
        return
    ch = bot.channels.get(ctx.where)
    names = sorted(n for n, p in ch.users.items() if '@' in p)
    ctx.tell('Ops in %s: %s' % (ctx.where, ', '.join(names) or 'none'))


@bot.on('JOIN')
async def welcome(bot, msg):
    if not bot.is_me(msg.nick):
        bot.notice(msg.nick, 'Welcome to %s, %s.' % (msg.target, msg.nick))


bot.run()

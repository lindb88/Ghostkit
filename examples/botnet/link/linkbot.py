"""linkbot: an IRC bot that works alone, and links with copies of itself when you give it a role.

Alone (no --role), it is an ordinary bot: !hello, and !remember / !recall / !forget for notes. With a role,
the bots link (botlink.py: TLS with a pinned certificate, a shared-secret login) and act as one team:

- one bot answers in each channel: the hub names a "main" bot per channel (itself first, then the leaves by
  name), and only that bot answers there;
- notes are shared: one remembered on any bot is known to all, and a bot that was away catches up when it
  links again (each note has its own timestamp; the newest change wins);
- when a leaf cannot reach the hub, it falls back to the rule of teambot.py (--team: the first of the team in
  the channel answers), so a channel is never left without an answer.

    # alone
    python3 linkbot.py --name Solo --port 6667

    # a hub and a leaf (on one machine: the leaf reads the hub's certificate file to pin it)
    python3 linkbot.py --name Hub --role hub --link-port 7000 --cert-dir hubcert --secret-file example.secret
    python3 linkbot.py --name Leaf1 --role leaf --link-port 7000 --hub-cert hubcert/link.crt \\
        --secret-file example.secret --team Hub,Leaf1

    # a leaf on another machine: the fingerprint, taken on the hub's machine with
    #   openssl x509 -in hubcert/link.crt -outform DER | sha256sum
    python3 linkbot.py --name Leaf2 --role leaf --hub hub.example.org --link-port 7000 \\
        --fingerprint 3f2a...  --secret-file my.secret --team Hub,Leaf1,Leaf2

Test: python3 -m irclab run linkbot.scenario   (and linkbot-alone.scenario)
Part of ghostkit (MIT license).
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, HERE)
from irctools import Bot  # noqa: E402
import botlink  # noqa: E402

ap = argparse.ArgumentParser(description='An IRC bot that links with copies of itself (optional).')
ap.add_argument('--name', required=True, help='nick, and name on the botnet')
ap.add_argument('--server', default='127.0.0.1')
ap.add_argument('--port', type=int, default=6667)
ap.add_argument('--channel', default='#test', help='channels, comma separated')
ap.add_argument('--notes', help='a file to keep the notes in (default: in memory only)')
ap.add_argument('--role', choices=('hub', 'leaf'), help='link with other copies (leave out to run alone)')
ap.add_argument('--link-port', type=int, default=7000)
ap.add_argument('--hub', default='127.0.0.1', help='leaf: the hub\'s address')
ap.add_argument('--secret-file', help='the shared secret (the same on every bot), first line of this file')
ap.add_argument('--cert-dir', help='hub: its certificate and key (made here if missing)')
ap.add_argument('--hub-cert', help='leaf on the hub\'s machine: pin the certificate in this file')
ap.add_argument('--fingerprint', help='leaf elsewhere: the hub certificate\'s SHA-256')
ap.add_argument('--team', default='', help='leaf: the team in order, for when the hub is out of reach')
ap.add_argument('--retry-max', type=int, default=300, help='leaf: longest wait between link attempts (s)')
ap.add_argument('--fast', action='store_true', help='send fast (for tests)')
a = ap.parse_args()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

chans = [c.strip() for c in a.channel.split(',') if c.strip()]
bot = Bot(a.name, a.server, a.port, channels=chans, **({'interval': 0.05, 'burst': 20} if a.fast else {}))
notes = botlink.Shared(a.notes)
team = [n.strip() for n in a.team.split(',') if n.strip()]
link = None                     # botlink.Hub or botlink.Leaf, with a role
mains = {}                      # lower channel -> the bot that answers there (from the hub)
where = {}                      # hub: bot name -> its lower channels


# ---- who answers ------------------------------------------------------------------------------------------

def answers_in(chan):
    """True when this bot should answer in chan."""
    chan = bot.lower(chan)
    if link is None:
        return True                                         # alone: always
    if isinstance(link, botlink.Leaf) and not link.linked:
        return fallback_turn(chan)                          # no hub: teambot's rule
    if isinstance(link, botlink.Hub):
        pick_mains()                                        # the hub decides, from what it knows right now
    return mains.get(chan) == a.name


def fallback_turn(chan):
    present = bot.channels.get(chan)
    if present is None:
        return False
    for nick in team:
        if bot.is_me(nick):
            return True
        if bot.lower(nick) in present.users:
            return False
    return not team                                         # no team given: answer


def pick_mains():
    """Hub: one bot per channel; the hub first, then the leaves by name. Told to every leaf when it changes."""
    where[a.name] = {bot.lower(c) for c in bot.channels}
    order = [a.name] + sorted(n for n in where if n != a.name and n in link.leaves)
    new = {}
    for name in order:
        for c in where.get(name, ()):
            new.setdefault(c, name)
    if new != mains:
        mains.clear()
        mains.update(new)
        link.broadcast({'t': 'mains', 'mains': mains})


# ---- link messages ----------------------------------------------------------------------------------------

def hub_message(name, m):
    t = m.get('t')
    if t == 'status':
        # about the leaf that sent it, by the name it logged in with: never a name inside the message
        got = m.get('chans')
        if not isinstance(got, list) or not all(isinstance(c, str) for c in got):
            raise TypeError('status: chans must be a list of channel names')
        where[name] = {bot.lower(c) for c in got[:500]}
        pick_mains()
    # "put" is merged by botlink already. Anything else from a leaf is not an order: a leaf gives no orders.


def hub_link(name):
    link.send(name, {'t': 'mains', 'mains': mains})


def hub_unlink(name):
    where.pop(name, None)
    pick_mains()


def leaf_message(m):
    if m.get('t') == 'mains':                               # only the hub can send this: a leaf links nowhere else
        got = m.get('mains')
        if not isinstance(got, dict):
            raise TypeError('mains must be an object')
        mains.clear()
        mains.update({str(k): str(v) for k, v in got.items()})


def leaf_link():
    send_status(force=True)


def leaf_unlink():
    mains.clear()


_last_status = [None]


def send_status(force=False):
    mine = sorted(bot.lower(c) for c in bot.channels)
    if force or mine != _last_status[0]:
        _last_status[0] = mine
        if isinstance(link, botlink.Leaf):
            link.send({'t': 'status', 'chans': mine})
        elif isinstance(link, botlink.Hub):
            pick_mains()


async def watch_channels():
    while True:
        try:
            send_status()
        except Exception:
            logging.exception('watching channels')
        await asyncio.sleep(1)


def share(change):
    if link is not None:
        link.put(change)


# ---- commands ---------------------------------------------------------------------------------------------

@bot.command('!hello')
async def hello(ctx):
    if ctx.where and not answers_in(ctx.where):
        return
    ctx.reply('Hello, %s! (from %s)' % (ctx.nick, bot.nick))


@bot.command('!remember')
async def remember(ctx):
    if ctx.where and not answers_in(ctx.where):
        return
    key, _, text = (ctx.args or '').partition(' ')
    if not key or not text.strip():
        ctx.reply('Usage: !remember <word> <text>')
        return
    share(notes.set(key.lower()[:30], text.strip()[:300]))
    ctx.reply('Remembered %s.' % key.lower()[:30])


@bot.command('!recall')
async def recall(ctx):
    if ctx.where and not answers_in(ctx.where):
        return
    key = (ctx.args or '').split(' ')[0].lower()
    text = notes.get(key)
    ctx.reply('%s: %s' % (key, text) if text else 'I know nothing about %s.' % (key or 'that'))


@bot.command('!forget')
async def forget(ctx):
    if ctx.where and not answers_in(ctx.where):
        return
    key = (ctx.args or '').split(' ')[0].lower()
    if notes.get(key) is None:
        ctx.reply('I know nothing about %s.' % (key or 'that'))
        return
    share(notes.delete(key))
    ctx.reply('Forgot %s.' % key)


@bot.command('!bots')
async def bots(ctx):
    if ctx.where and not answers_in(ctx.where):
        return
    if link is None:
        ctx.reply('I am on my own.')
    elif isinstance(link, botlink.Hub):
        ctx.reply('Linked to %s: %s' % (a.name, ', '.join([a.name] + sorted(link.leaves))))
    elif link.linked:
        ctx.reply('Linked to %s.' % link.hub_name)
    else:
        ctx.reply('I cannot reach the hub right now.')


# ---- starting ---------------------------------------------------------------------------------------------

def read_secret():
    if not a.secret_file:
        sys.exit('--role needs --secret-file (the same secret on every bot)')
    with open(a.secret_file) as f:
        return f.readline().strip()


async def main():
    global link
    loop = asyncio.get_event_loop()
    for sig in ('SIGINT', 'SIGTERM'):
        try:
            loop.add_signal_handler(getattr(signal, sig), bot.stop, 'Shutting down')
        except (NotImplementedError, AttributeError, RuntimeError):
            pass
    tasks = []
    if a.role == 'hub':
        if not a.cert_dir:
            sys.exit('--role hub needs --cert-dir')
        crt, key = os.path.join(a.cert_dir, 'link.crt'), os.path.join(a.cert_dir, 'link.key')
        if not os.path.exists(crt):
            botlink.make_cert(crt, key, a.name)
            logging.info('Made a link certificate; its fingerprint (for leaves elsewhere): %s',
                         botlink.cert_fingerprint(crt))
        link = botlink.Hub(a.name, read_secret(), crt, key, port=a.link_port, shared=notes,
                           on_message=hub_message, on_link=hub_link, on_unlink=hub_unlink)
        await link.start()
        tasks.append(watch_channels())
    elif a.role == 'leaf':
        if a.hub_cert:
            fp = (lambda: botlink.cert_fingerprint(a.hub_cert))     # read each try: the hub may make it later
        elif a.fingerprint:
            fp = a.fingerprint
        else:
            sys.exit('--role leaf needs --fingerprint (or --hub-cert on the hub\'s machine)')
        link = botlink.Leaf(a.name, read_secret(), '%s:%d' % (a.hub, a.link_port), fp, shared=notes,
                            on_message=leaf_message, on_link=leaf_link, on_unlink=leaf_unlink)
        link.retry_first, link.retry_max = min(5, a.retry_max), a.retry_max
        tasks += [link.run(), watch_channels()]
    running = [asyncio.ensure_future(t) for t in tasks]
    try:
        await bot.run_forever()                 # the link lives as long as the bot
    finally:
        for t in running:
            t.cancel()
        if link is not None:
            link.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""teambot: the simplest botnet. Several copies of one bot share a channel; exactly one answers each command,
and when it goes, the next one takes over by itself. No link between the bots is needed.

The rule (a few lines in any language): the bots know the team's nicks, in order. In a channel, a bot answers
only if no team member before it in that order is in the channel. Every bot sees the same channel, so they all
agree, without talking to each other.

    python3 teambot.py --nick Team1 --team Team1,Team2 --port 6667
    python3 teambot.py --nick Team2 --team Team1,Team2 --port 6667

Test: python3 -m irclab run teambot.scenario  (Team1 answers; stop it, and Team2 does)
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from irctools import Bot  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--server', default='127.0.0.1')
ap.add_argument('--port', type=int, default=6667)
ap.add_argument('--nick', required=True)
ap.add_argument('--team', required=True, help='the team, in order: Team1,Team2,Team3')
ap.add_argument('--channel', default='#test')
a = ap.parse_args()
team = [n.strip() for n in a.team.split(',') if n.strip()]

bot = Bot(a.nick, a.server, a.port, channels=[a.channel], interval=0.05, burst=20)


def my_turn(chan):
    """True when no team member before me in the order is in the channel."""
    present = bot.channels.get(chan)
    if present is None:
        return False
    for nick in team:
        if bot.is_me(nick):
            return True                                 # everyone before me is away
        if bot.lower(nick) in present.users:
            return False                                # someone before me is here: they answer
    return False                                        # I am not in the team list


@bot.command('!hello')
async def hello(ctx):
    if ctx.where and not my_turn(ctx.where):
        return
    ctx.reply('Hello, %s! (from %s)' % (ctx.nick, bot.nick))


bot.run()

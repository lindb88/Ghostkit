"""irctools: the pieces of an IRC bot, in Python 3.8+ with the standard library only.

    message   parse() and build() IRC lines (IRCv3 tags too), split_text() for long messages
    casemap   lower(), equal(), CaseDict: names compared the way the server does
    masks     match('*!*bob@*', mask), ban_mask()
    sendq     SendQueue: flood-safe sending
    client    Bot: connecting, CAP, SASL, reconnects, channels, commands

Taken from ghostbot, where they run a botnet. Part of ghostkit (MIT license). See docs/irctools.md.
"""

from .casemap import CaseDict, equal, lower
from .client import Bot, Context
from .masks import ban_mask, is_channel, match
from .message import Message, build, clean, parse, split_text
from .sendq import SendQueue

__version__ = '0.1.0'
__all__ = ['Bot', 'Context', 'Message', 'parse', 'build', 'clean', 'split_text', 'lower', 'equal', 'CaseDict',
           'match', 'ban_mask', 'is_channel', 'SendQueue', '__version__']

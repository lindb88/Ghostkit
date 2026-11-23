"""irctools: the pieces of an IRC bot, in Python 3.8+ with the standard library only.

    message   parse() and build() IRC lines (IRCv3 tags too), split_text() for long messages
    casemap   lower(), equal(), CaseDict: names compared the way the server does
    masks     match('*!*bob@*', mask), ban_mask()
    sendq     SendQueue: flood-safe sending
    safefetch fetch() and page_title() for addresses people post, never reaching your own network
    client    Bot: connecting, CAP, SASL or QuakeNet's Q, reconnects, channels, commands, status.json

Taken from ghostbot, where they run a botnet. Part of ghostkit (MIT license). See docs/irctools.md.
"""

from .casemap import CaseDict, equal, lower
from .client import Bot, Context, q_password_hash, q_response
from .masks import ban_mask, is_channel, match
from .message import Message, build, clean, parse, split_text
from .sendq import SendQueue
from . import safefetch

__version__ = '0.1.1'
__all__ = ['Bot', 'Context', 'Message', 'parse', 'build', 'clean', 'split_text', 'lower', 'equal', 'CaseDict',
           'match', 'ban_mask', 'is_channel', 'SendQueue', 'q_password_hash', 'q_response', 'safefetch', '__version__']

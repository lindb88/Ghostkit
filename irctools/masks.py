"""irctools.masks: nick!user@host masks, as in bans and access lists.

    match('*!*bob@*.example.com', 'Bob!~bob@home.example.com')   # True: * and ?, IRC case rules
    ban_mask('Bob', '~bob', 'home.example.com')                    # '*!*bob@home.example.com'
    ban_mask('Bob', '~bob', 'home.example.com', 'host')            # '*!*@home.example.com'
"""

import re

from .casemap import lower

_cache = {}


def match(pattern, text, mapping='rfc1459'):
    """Does text (nick!user@host) fit the wildcard pattern (* any run, ? one character)?"""
    key = (pattern, mapping)
    rx = _cache.get(key)
    if rx is None:
        p = re.escape(lower(pattern, mapping)).replace(r'\*', '.*').replace(r'\?', '.')
        rx = re.compile('^' + p + '$', re.S)
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = rx
    return bool(rx.match(lower(text, mapping)))


def ban_mask(nick, user, host, kind='user'):
    """A mask for this person: 'user' *!*ident@host (the ~ dropped, as many servers add it), 'host' *!*@host,
    'nick' nick!*@*, 'exact' nick!user@host."""
    user = (user or '*').lstrip('~') or '*'
    host = host or '*'
    if kind == 'host':
        return '*!*@%s' % host
    if kind == 'nick':
        return '%s!*@*' % nick
    if kind == 'exact':
        return '%s!%s@%s' % (nick, user, host)
    return '*!*%s@%s' % (user, host)


def is_channel(name, types='#&'):
    """Is this a channel name (by the server's CHANTYPES)?"""
    return bool(name) and name[0] in types

"""irctools.casemap: IRC nick and channel names compared the way the server does.

On most networks [ ] \\ ~ are the upper case of { } | ^ (CASEMAPPING=rfc1459), so 'Bob[away]' and
'bob{away}' are the same nick. The server says which rules it uses in its 005 (ISUPPORT) lines.

    lower('Bob[away]')                    # 'bob{away}'
    equal('Bob[away]', 'BOB{AWAY}')       # True
"""

_RFC1459 = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\~', 'abcdefghijklmnopqrstuvwxyz{}|^')
_STRICT = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ[]\\', 'abcdefghijklmnopqrstuvwxyz{}|')
_ASCII = str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
CASEMAPS = {'rfc1459': _RFC1459, 'strict-rfc1459': _STRICT, 'ascii': _ASCII}


def lower(s, mapping='rfc1459'):
    """s in lower case by mapping (rfc1459, strict-rfc1459 or ascii; others count as rfc1459)."""
    return (s or '').translate(CASEMAPS.get(mapping, _RFC1459))


def equal(a, b, mapping='rfc1459'):
    return lower(a, mapping) == lower(b, mapping)


class CaseDict(dict):
    """A dict whose keys are nicks or channels: d['Bob[x]'] and d['bob{x}'] are the same entry."""

    def __init__(self, mapping='rfc1459', *a, **kw):
        self.mapping = mapping
        super().__init__()
        for k, v in dict(*a, **kw).items():
            self[k] = v

    def _k(self, k):
        return lower(k, self.mapping)

    def __setitem__(self, k, v):
        super().__setitem__(self._k(k), v)

    def __getitem__(self, k):
        return super().__getitem__(self._k(k))

    def __delitem__(self, k):
        super().__delitem__(self._k(k))

    def __contains__(self, k):
        return super().__contains__(self._k(k))

    def get(self, k, default=None):
        return super().get(self._k(k), default)

    def pop(self, k, *default):
        return super().pop(self._k(k), *default)

    def setdefault(self, k, default=None):
        return super().setdefault(self._k(k), default)

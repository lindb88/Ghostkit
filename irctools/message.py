"""irctools.message: IRC lines in and out (with IRCv3 message tags).

    m = parse('@account=alice :Alice!alice@host PRIVMSG #test :hello there')
    m.nick, m.command, m.target, m.text, m.tags['account']      # 'Alice', 'PRIVMSG', '#test', 'hello there', 'alice'
    build('PRIVMSG', '#test', 'hello there')                     # 'PRIVMSG #test :hello there'

build() never lets a line break, carriage return or NUL through, so text from users cannot add IRC lines of
its own (the classic injection bug). split_text() cuts long text into pieces a server will take whole.
"""

_TAG_ESCAPES = {':': ';', 's': ' ', 'r': '\r', 'n': '\n', '\\': '\\'}
_TAG_ENCODE = {';': '\\:', ' ': '\\s', '\r': '\\r', '\n': '\\n', '\\': '\\\\'}
_BAD = str.maketrans({'\r': ' ', '\n': ' ', '\0': ' '})


def _unescape_tag(v):
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == '\\' and i + 1 < len(v):
            out.append(_TAG_ESCAPES.get(v[i + 1], v[i + 1]))
            i += 2
        elif c == '\\':
            i += 1                                     # a lone backslash at the end is dropped
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _escape_tag(v):
    return ''.join(_TAG_ENCODE.get(c, c) for c in str(v))


class Message:
    """A parsed line. .nick/.user/.host come from the prefix; .text is the last parameter."""
    __slots__ = ('tags', 'prefix', 'nick', 'user', 'host', 'command', 'params', 'raw')

    def __init__(self):
        self.tags, self.prefix, self.nick, self.user, self.host = {}, '', '', '', ''
        self.command, self.params, self.raw = '', [], ''

    def arg(self, i, default=''):
        return self.params[i] if len(self.params) > i else default

    @property
    def text(self):
        return self.params[-1] if self.params else ''

    @property
    def target(self):
        return self.params[0] if self.params else ''

    @property
    def mask(self):
        return '%s!%s@%s' % (self.nick, self.user, self.host)

    def __repr__(self):
        return 'Message(%r)' % self.raw


def parse(line):
    """A Message from one IRC line (without its line ending)."""
    m = Message()
    m.raw = line = line.rstrip('\r\n')
    if line.startswith('@'):
        tagstr, _, line = line[1:].partition(' ')
        for part in tagstr.split(';'):
            if part:
                k, _, v = part.partition('=')
                m.tags[k] = _unescape_tag(v)
    line = line.lstrip(' ')
    if line.startswith(':'):
        m.prefix, _, line = line[1:].partition(' ')
        nick, _, rest = m.prefix.partition('!')
        m.nick = nick
        if rest:
            m.user, _, m.host = rest.partition('@')
        elif '@' in nick:
            m.nick, _, m.host = nick.partition('@')
    if ' :' in line:
        head, _, trail = line.partition(' :')
        params = head.split() + [trail]
    elif line.startswith(':'):
        params = [line[1:]]
    else:
        params = line.split()
    if params:
        m.command = params.pop(0).upper()
    m.params = params
    return m


def clean(text):
    """Text that cannot break out of its line: CR, LF and NUL become spaces."""
    return str(text).translate(_BAD)


def build(command, *params, tags=None):
    """One IRC line: the last parameter gets a ':' when it needs one; CR, LF and NUL become spaces."""
    parts = [clean(command)]
    ps = [clean(p) for p in params]
    for i, p in enumerate(ps):
        last = i == len(ps) - 1
        if last and (not p or ' ' in p or p.startswith(':')):
            parts.append(':' + p)
        else:
            if not p or ' ' in p or p.startswith(':'):
                raise ValueError('only the last parameter may be empty, have spaces or start with ":": %r' % p)
            parts.append(p)
    line = ' '.join(parts)
    if tags:
        line = '@' + ';'.join(k if v in (None, True, '') else '%s=%s' % (k, _escape_tag(v))
                              for k, v in tags.items()) + ' ' + line
    return line


def split_text(text, limit=400):
    """Pieces of text of at most limit bytes (UTF-8), cut at spaces when it can, never inside a character."""
    text = clean(text)
    out = []
    while len(text.encode('utf-8')) > limit:
        b = text.encode('utf-8')[:limit]
        piece = b.decode('utf-8', 'ignore')            # never half a character
        cut = piece.rfind(' ')
        if cut > len(piece) // 2:
            piece = piece[:cut]
        out.append(piece.rstrip())
        text = text[len(piece):].lstrip()
    if text or not out:
        out.append(text)
    return out

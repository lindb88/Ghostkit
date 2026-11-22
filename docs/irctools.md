# irctools: the pieces of an IRC bot

The parts every IRC bot needs and most get subtly wrong, in Python 3.8+
with the standard library only. Use the whole `Bot`, or just the pieces in
a bot of your own.

## A bot

```python
from irctools import Bot

bot = Bot('MyBot', 'irc.libera.chat', 6697, tls=True, channels=['#mytest'],
          sasl=('myaccount', 'mypassword'))

@bot.command('!hello')
async def hello(ctx):
    ctx.reply('Hello, %s!' % ctx.nick)

@bot.command('!echo')
async def echo(ctx):
    ctx.reply(ctx.args or 'Echo what?')

@bot.on('JOIN')
async def welcome(bot, msg):
    if not bot.is_me(msg.nick):
        bot.notice(msg.nick, 'Welcome to %s!' % msg.target)

bot.run()
```

On **QuakeNet**, which has no SASL, log in to Q instead:

```python
bot = Bot('MyBot', 'irc.quakenet.org', 6667, channels=['#mytest'],
          q_auth=('MyAccount', os.environ['Q_PASSWORD']))
```

The bot logs in with Q's CHALLENGEAUTH (HMAC-SHA-256, as QuakeNet
documents it: the password itself is never sent), sets `+x` so its host
becomes `MyAccount.users.quakenet.org`, and only then joins its channels,
so its real address never shows in them. If the login fails, it joins
anyway and logs a warning; with `q_required=True` it stays out instead.
`hide_host=False` skips `+x`. Only notices from the real Q
(`Q!TheQBot@CServe.quakenet.org`) count, not from someone who takes the
nick Q on another network.

What `Bot` takes care of:

- **Connecting**: TLS (verified; `tls_verify=False` only for a test
  server), QuakeNet's Q login with a hidden host, IRCv3 capabilities (CAP LS 302: `sasl`, `account-tag`,
  `extended-join`, `multi-prefix`, `away-notify`, `account-notify`,
  `server-time`, `message-tags`, whichever the server offers), SASL PLAIN,
  another nick when its nick is taken (`MyBot_`).
- **Staying connected**: answers PING; sends its own when the server has
  been quiet for 2 minutes and reconnects after 3; reconnects with growing
  waits (5 s up to 5 minutes, with jitter, longer when the server says
  "too fast"); rejoins its channels, and a channel it was kicked from.
- **The server's rules**: ISUPPORT `CASEMAPPING`, `CHANTYPES` and `PREFIX`,
  so `Bob[x]` and `bob{x}` are the same nick where the server says so.
- **Who is where**: `bot.channels['#chan'].users` (lower nick: prefix
  characters such as `@`), topics, `bot.accounts` (from `account-tag`,
  `extended-join` and `account-notify`), `bot.is_op(chan, nick)`.
- **Sending safely**: `say` and `notice` go through a flood-safe queue,
  are split into pieces that fit (never inside a UTF-8 character), and
  never contain a line break: a user's text cannot become an IRC command
  of its own.

`Bot(nick, host, port=6667, tls=False, channels=(), sasl=None,
realname=..., username=None, password=None, prefix_chars='!',
tls_verify=True, burst=4, interval=1.5, q_auth=None, hide_host=True,
q_required=False)`; `bot.account` is the Q account once logged in

- `@bot.command('!word')`: an async function taking a `Context`: `.nick`,
  `.account`, `.args` (the rest of the line), `.where` (the channel, or
  `None` in private), `.msg`; `.reply(text)` answers where it was typed,
  `.tell(text)` only the person. In a private message the `!` may be left
  out.
- `@bot.on('PRIVMSG')`: an async function `(bot, msg)` for every such line;
  numerics too (`'001'`, `'433'`), `'*'` for every line.
- `say(target, text)`, `notice`, `join(chan, key)`, `part`, `send(raw)`,
  `stop(reason)`, `run()` (blocks), or `await run_forever()` inside your own
  asyncio program.

- `status_file(path, every=30, title=None, hide=(), network=None,
  version=None, note=None)`: write `status.json` for webstatus's page while
  running; `status()` gives the same as a dict. See
  [webstatus.md](webstatus.md).

## The pieces

```python
from irctools import parse, build, split_text

m = parse('@account=bob :Bob!~bob@host PRIVMSG #chan :hi there')
m.nick, m.user, m.host      # 'Bob', '~bob', 'host'
m.command, m.target, m.text # 'PRIVMSG', '#chan', 'hi there'
m.tags['account']           # 'bob'
m.arg(1, default)           # a parameter, or the default

build('PRIVMSG', '#chan', 'hi there')     # 'PRIVMSG #chan :hi there'
build('PRIVMSG', '#chan', 'a\r\nQUIT')    # 'PRIVMSG #chan :a  QUIT' (CR, LF, NUL become spaces)
split_text(long_text, 400)                # pieces of at most 400 UTF-8 bytes, at spaces
```

```python
from irctools import lower, equal, CaseDict, match, ban_mask

equal('Bob[x]', 'BOB{X}')                     # True (rfc1459)
d = CaseDict('rfc1459'); d['Bob'] = 1; d['BOB']   # 1
match('*!*bob@*.example.com', 'Bob!~bob@a.example.com')   # True
ban_mask('Bob', '~bob', 'a.example.com')       # '*!*bob@a.example.com'
ban_mask('Bob', '~bob', 'a.example.com', 'host')   # '*!*@a.example.com'
```

```python
from irctools import SendQueue

q = SendQueue(writer, burst=4, interval=1.5)  # 4 lines at once, then one every 1.5 s
asyncio.ensure_future(q.run())
q.put('PRIVMSG #chan :hi')
q.put('PONG :x', urgent=True)                 # before the rest
```

## Testing

A bot built on irctools tests well with irclab. See
`examples/echobot.py` and `examples/echobot.scenario`:

```
python3 -m irclab run examples/echobot.scenario
```

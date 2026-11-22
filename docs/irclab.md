# irclab: test your bot without a network

irclab is a small IRC server and scripted users that run on your own
machine. Your bot connects to it like to any network; nothing goes on the
internet, nobody sees your test bot flood or crash, and a test takes seconds.

It works for a bot in **any language**: you describe what should happen in
a plain text *scenario*, and irclab starts your bot, plays the users, and
reports each step. For bots in Python you can also script it directly.

Python 3.8 or newer, standard library only.

## A scenario

```
# hello.scenario
bot python3 mybot.py --server 127.0.0.1 --port {port}
wait MyBot in #test

user Alice
Alice join #test
Alice say #test !hello
expect Alice from MyBot in #test "Hello, Alice"

# it must not answer what is not a command
Alice say #test "just talking"
refute Alice from MyBot in #test for 2
```

```
$ python3 -m irclab run hello.scenario
ok 1 - bot python3 mybot.py --server 127.0.0.1 --port {port}
ok 2 - wait MyBot in #test
...
7 of 7 passed
```

The exit status is 0 when every step passed, so it fits in CI. The run stops
at the first failure and shows what the user received and the last lines
your bot printed:

```
not ok 4 - expect Alice from MyBot in #test 'Hello, Alice' (line 7)
#   Alice: timed out waiting for a message in #test from MyBot containing 'Hello, Alice'
#   --- the last lines it got ---
#   :Alice!alice@alice.host JOIN #test
#   :MyBot!mybot@mybot.host PRIVMSG #test :Hi Alice
#   --- the bot's last output ---
#   connected; joined #test
```

## The steps

One step per line. A line starting with `#` is a comment; a `#` later in a
line is part of it (channel names), so comments go on lines of their own.
Quotes group words: `"two words"` or `'say "hi"'`.

| Step | |
|---|---|
| `server plain\|libera\|quakenet` | the network's kind (first line, if at all; `plain` when left out) |
| `account <name> <password>` | a services account (libera, quakenet) |
| `chanlev <#chan> <account> <flags>` | services access in a channel: ChanServ or Q gives op (`o`) and invite |
| `bot <command> [args]` | start your bot; `{port}` and `{host}` are the server's |
| `user <nick> [ident]` | a user connects; their host is `<ident>.host` (`alice.host`) |
| `<nick> join <#chan> [key]` | |
| `<nick> part <#chan>` | |
| `<nick> say <target> <text>` | a message to a channel or a nick |
| `<nick> notice <target> <text>` | |
| `<nick> nick <newnick>` | |
| `<nick> quit` | |
| `<nick> send <raw IRC line>` | anything else, e.g. `Alice send "MODE #test +v Bob"` |
| `<nick> identify <account> <password>` | log in with NickServ (libera) |
| `<nick> qauth <account> <password>` | log in with Q's CHALLENGEAUTH (quakenet) |
| `wait <nick> online` | until that nick is connected |
| `wait <nick> in <#chan>` | until it is in the channel |
| `wait <nick> op <#chan>` | until it has ops there |
| `expect <user> from <nick> [in <#chan> \| private] [notice] [<text>]` | the user gets a message from nick (containing the text) |
| `refute <user> from <nick> [in <#chan> \| private] [<text>] for <seconds>` | and gets no such message for that long |
| `sleep <seconds>` | |

`wait` and `expect` give up after 10 seconds; end the step with
`within <seconds>` for another limit (`wait MyBot in #test within 60`).

**The bot step** runs in the scenario file's folder. Its words are read as
the scenario reads them (quotes group, `#test` is a channel), with no shell
in between: no pipes or `&&`. For more setup, point it at a script.

## The networks

- **plain**: no services. CAP LS 302 with account-notify, extended-join,
  account-tag, multi-prefix and away-notify. The first in a channel gets ops.
- **libera**: plain plus SASL (PLAIN, and EXTERNAL with client certificates
  over TLS), NickServ (IDENTIFY, REGISTER, VERIFY, CERT ADD, REGAIN) and
  ChanServ (OP, INVITE, UNBAN, by the flags from `chanlev`).
- **quakenet**: like ircu, no CAP; Q with CHALLENGE/CHALLENGEAUTH (HMAC-SHA256),
  HELLO, OP, INVITE, UNBANME, and `+x` hidden hosts; users start `+i`.

All of them: channel modes b, i, k, l, m, n, o, p, r, s, t and v; WHO (with
WHOX), WHOIS, NAMES, INVITE, KICK, TOPIC, ISON. `server.received` keeps
every line as `(nick, line)` for checks; `server.throttle` (seconds between
connections) and `server.banned_hosts` (K-lines) test reconnect handling.

They are close enough to the real ones for a bot's logic, not a full
implementation: test flood limits and odd server replies against the real
network too.

## From Python

```python
import asyncio
from irclab import IRCServer, User, until

async def main():
    server = await IRCServer('plain').start()          # port 0: a free port, in server.port
    # ... start your bot against server.port ...
    alice = await User('Alice').connect(server.port)
    await alice.join('#test')
    alice.say('#test', '!hello')
    line = await alice.expect(lambda l: l.command == 'PRIVMSG' and l.nick == 'MyBot', timeout=5)
    assert line.text == 'Hello, Alice!'
    # or: await alice.reply('MyBot', contains='Hello', where='#test')
    await until(lambda: server.is_op('#test', 'MyBot'), 10, 'MyBot opped')
    alice.close()
    await server.stop()

asyncio.run(main())
```

`IRCServer(mode, name='irc.test', on_line=None)`: `start(port=0, tls=None,
host='127.0.0.1')`, `stop()`, `add_account()`, `set_chanlev()`,
`client(nick)`, `chan(name)` (its `.members`), `is_op(chan, nick)`,
`kill(nick)`. `on_line(client, line)` sees every line a client sends.

`User(nick, ident=None)`: `connect(port)`, `say`, `notice`, `join` (async),
`part`, `send`, `mark()` (forget what came so far), `expect(pred, timeout)`,
`reply(frm, contains, kind, timeout, where)`, `ask(bot, text, contains)`
(a private question, paced like a person), `identify`, `q_auth`, `close`.
Each `Line` has `.nick`, `.command`, `.params`, `.target`, `.text`, `.raw`.

`Runner(text, out=print, base=folder, on_server=fn, env={...})` runs a
scenario from Python; `on_server(server)` is called once the port is known,
before any step, for a tool that writes the bot's config (ghostbot's plugin
kit does this).

## One file

`python3 tools/bundle_irclab.py out.py` writes irclab as a single module,
for a project that wants to ship it inside itself.

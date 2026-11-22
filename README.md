# ghostkit

Tools for people building IRC bots: test them on your own machine, start
from pieces that get the details right, and ship updates safely. Taken from
ghostbot, a Python botnet that runs on QuakeNet together with eggdrops, where
all of this is in daily use.

Python 3.8+ and Tcl 8.6/9, with nothing to install. MIT license.

| | | |
|---|---|---|
| **irclab** | a test IRC server and scripted users; test a bot in any language with a plain text scenario | [docs/irclab.md](docs/irclab.md) |
| **irctools** | the pieces of a Python IRC bot: parsing, case rules, masks, flood-safe sending, and a `Bot` that does the housekeeping | [docs/irctools.md](docs/irctools.md) |
| **eggkit** | for eggdrop scripts: safe helpers, Ed25519 signature checks in plain Tcl, and signed self-updates | [docs/eggkit.md](docs/eggkit.md) |
| **ghostbot plugins** | writing plugins for ghostbot, with an example | [docs/ghostbot-plugins.md](docs/ghostbot-plugins.md) |

## Five minutes

Test the example bot against the test server:

```
python3 -m irclab run examples/echobot.scenario
```

```
ok 1 - bot python3 echobot.py --port {port} --fast --channel #test
ok 2 - wait EchoBot in #test
ok 3 - user Alice
...
15 of 15 passed
```

Now test your own bot. Write `mybot.scenario` next to it:

```
bot python3 mybot.py --server 127.0.0.1 --port {port}
wait MyBot in #test
user Alice
Alice join #test
Alice say #test !hello
expect Alice from MyBot in #test "Hello"
```

and run `python3 -m irclab run mybot.scenario`. Your bot can be in any
language; it only has to take the server and port from its command line
(or a config file the `bot` line points at).

### Any language

irclab doesn't care what your bot is written in: it is a real IRC server on
your own machine, and your bot connects to it like to any network. Only
irclab itself needs Python (already on almost every Linux and Mac). Two small
bots in other languages, each with its test, are in `examples/`:

```
python3 -m irclab run examples/node/hellobot.scenario     # Node.js
python3 -m irclab run examples/go/hellobot.scenario       # Go
```

The scenario is the same text whatever the language; only the `bot` line
changes:

```
bot node hellobot.js --port {port}
bot go run hellobot.go -port {port}
bot perl mybot.pl --port {port}
bot ./target/release/mybot --port {port}
bot java -jar mybot.jar --port {port}
```

For an eggdrop, point the `bot` line at a script that writes a config with
`server add 127.0.0.1 {port}` and starts `./eggdrop -n -m`.

Or start a Python bot on irctools:

```python
from irctools import Bot

bot = Bot('MyBot', 'irc.libera.chat', 6697, tls=True, channels=['#mytest'])

@bot.command('!hello')
async def hello(ctx):
    ctx.reply('Hello, %s!' % ctx.nick)

bot.run()
```

Or make an eggdrop script update itself only with releases you signed:

```
python3 eggkit/sign.py keygen release.key        # once; prints your public key
python3 eggkit/sign.py sign release.key myscript.tcl
```

## What is here

```
irclab/        the test server, users and scenario runner   (python3 -m irclab run|serve)
irctools/      the bot pieces and Bot
eggkit/        eggkit-util.tcl, eggkit-ed25519.tcl, eggkit-update.tcl, sign.py
examples/      echobot.py + echobot.scenario (Python), node/ and go/ bots with their scenarios,
               eggdrop/greeter.tcl, ghostbot/quotes.py + its test
tests/         python3 tests/run.py
tools/         bundle_irclab.py: irclab as one file to ship inside a project
docs/
```

`python3 -m irclab serve` runs just the server (port 6667 by default), to
point a bot or an IRC client at by hand.

## Tests

```
python3 tests/run.py
```

The eggkit tests need a `tclsh` (set `TCLSH=/path/to/tclsh` if it is not on
the PATH) and use openssl and curl when they are there; what cannot run is
skipped, not failed.

## Security

See [SECURITY.md](SECURITY.md) for reporting a problem, and what the tools
do and do not protect against.

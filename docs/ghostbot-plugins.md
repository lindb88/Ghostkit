# Writing ghostbot plugins

ghostbot (2.29 and later) has the plugin kit built in, so it always matches
the bot you run:

```
./ghostbot.sh newplugin dice      # plugins/dice.py and plugins/tests/dice.scenario, ready to edit
./ghostbot.sh check dice          # reads it over: imports, blocking calls, name clashes, translations
./ghostbot.sh test dice           # a test IRC server and a throwaway copy of your bot play the scenario
```

The full guide ships with the bot: `docs/devkit.md` (the kit) and
`docs/plugins.md` (the plugin API). This page is the short version, plus an
example.

## The shape of a plugin

```python
# ghostbot plugin: dice
# Last changed: new

import random


def setup(bot):
    lib = bot.lib

    @bot.command('roll', USER, 'roll [sides]: roll a die', topic='Fun')
    async def roll(ctx):
        sides = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 6
        ctx.reply(T('{nick} rolls {n}.', nick=ctx.nick, n=random.randint(1, max(2, sides))))
```

- One file in `plugins/`; `setup(bot)` registers everything and is called
  again on every reload.
- The standard library only: bots have nothing else installed.
- `T('...')` for every text people read, with `{names}`, so it can be
  translated (Swedish ships with the bot).
- Levels: `USER`, `VIP`, `VOICE`, `OP`, `MODERATOR`, `OWNER`, `ADMIN`,
  `MASTER`, available without an import, like `T`.
- Never block: a network call or anything slow goes in a plain function run
  with `await lib.asyncio.get_event_loop().run_in_executor(None, fn, ...)`.
- In a botnet, every bot loads the plugin; answer in a channel only when
  `bot.botnet.answers_in(chan)`, so one bot answers.

## The example

`examples/ghostbot/quotes.py` is a complete plugin: a channel's quote
collection with levels (voice adds, ops remove), a saved file, numbers that
stay put, plurals in its texts. Its test is
`examples/ghostbot/tests/quotes.scenario`.

```
cp examples/ghostbot/quotes.py ~/ghostbot/plugins/
cp examples/ghostbot/tests/quotes.scenario ~/ghostbot/plugins/tests/
cd ~/ghostbot && ./ghostbot.sh check quotes && ./ghostbot.sh test quotes
```

The scenario steps are irclab's (see [irclab.md](irclab.md)), with two
differences: the test starts the bot itself (no `bot` step), and when it
starts, `TestBot` is opped in `#test` and `Owner`, logged in as the botnet
owner, is there too.

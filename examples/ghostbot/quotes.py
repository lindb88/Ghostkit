# ghostbot plugin: quotes, a channel's own collection (an example from ghostkit)
# Last changed: 0.1.0 2026-11-22 19:30 UTC
#
#   quote                 a random quote from the channel's collection
#   quote <number>        that one
#   quote <word>          a random one containing the word
#   quote add <text>      add one (voiced people and up)
#   quote del <number>    remove one (ops and up)
#   quote count           how many there are
#
# Install: copy this file to plugins/, then ./ghostbot.sh reload. Test: copy tests/quotes.scenario to
# plugins/tests/ and run ./ghostbot.sh test quotes. Saved in quotes.json in the bot's folder; MIT license.

import random
import time

MAX_LEN = 300
MAX_PER_CHANNEL = 5000


def setup(bot):
    lib = bot.lib
    fn = lib.path('quotes.json')
    st = bot.keep('quotes', {'data': None})
    if st['data'] is None:                                # first load since the start: read the file once
        st['data'] = lib.load_json(fn, {})

    def chan_quotes(chan):
        return st['data'].setdefault(bot.lower(chan), [])

    def save():
        lib.save_json(fn, st['data'])

    def show(ctx, n, q):
        ctx.reply(T('#{n}: {text} (added by {who})', n=n, text=q['text'], who=q['by']))

    @bot.command('quote', USER, 'quote [number|word|add <text>|del <number>|count]: the channel\'s quotes',
                 topic='Fun')
    async def quote(ctx):
        chan = ctx.channel
        if not chan:
            ctx.tell(T('Use it in a channel, or: quote #channel ...'))
            return
        if ctx.where and not bot.botnet.answers_in(chan):
            return                                        # another bot of the botnet answers here
        qs = chan_quotes(chan)
        args = [a for a in ctx.args if not bot.is_chan(a)] if not ctx.where else list(ctx.args)
        sub = args[0].lower() if args else ''
        if sub == 'add':
            if ctx.level < VOICE:
                ctx.tell(T('Adding quotes needs voice or higher in {chan}.', chan=chan))
                return
            text = ctx.rest.split(None, 1)[1].strip() if len(ctx.rest.split(None, 1)) > 1 else ''
            if not ctx.where and text.startswith(chan):
                text = text[len(chan):].strip()
            if not text:
                ctx.tell(T('Use: quote add <text>'))
                return
            if len(text) > MAX_LEN or len(qs) >= MAX_PER_CHANNEL:
                ctx.tell(T('That is too long (at most {n} characters), or the collection is full.', n=MAX_LEN))
                return
            qs.append({'text': text, 'by': ctx.nick, 'at': int(time.time())})
            save()
            ctx.reply(T('Added as #{n}.', n=len(qs)))
        elif sub == 'del':
            if ctx.level < OP:
                ctx.tell(T('Removing quotes needs op or higher in {chan}.', chan=chan))
                return
            if len(args) < 2 or not args[1].isdigit() or not 1 <= int(args[1]) <= len(qs):
                ctx.tell(T('Use: quote del <number> (1 to {n})', n=len(qs)))
                return
            qs[int(args[1]) - 1] = None                   # numbers stay the same for the others
            while qs and qs[-1] is None:
                qs.pop()
            save()
            ctx.reply(T('Removed #{n}.', n=args[1]))
        elif sub == 'count':
            n = sum(1 for q in qs if q)
            ctx.reply(T('{chan} has {n|# quote|# quotes}.', chan=chan, n=n))
        elif sub.isdigit():
            n = int(sub)
            if 1 <= n <= len(qs) and qs[n - 1]:
                show(ctx, n, qs[n - 1])
            else:
                ctx.reply(T('There is no quote #{n}.', n=n))
        else:
            found = [(i + 1, q) for i, q in enumerate(qs) if q and (not sub or sub in q['text'].lower())]
            if found:
                show(ctx, *random.choice(found))
            elif sub:
                ctx.reply(T('No quote contains "{word}".', word=args[0]))
            else:
                ctx.reply(T('No quotes yet. Add one: quote add <text>'))

# Building an IRC botnet

How to run several bots as one team, whatever language they are written in.
The ideas come from ghostbot, a botnet of Python bots and eggdrops that runs
on QuakeNet, and from the eggdrop tradition it grew out of.

**What "botnet" means here.** On IRC, a botnet is a group of *your own*
bots, on your own machines or accounts, linked to work together: keeping a
channel safe when one bot drops, sharing the load, answering once. It has
nothing to do with hijacked computers. Everything below assumes bots you
run, within the rules of the networks they are on.

## Why more than one bot

- **A channel survives a bot going down.** A server split, a reboot or a
  crash leaves the channel without its bot; with two on different machines,
  one is still there to keep ops and answer.
- **More channels than one connection allows.** Networks limit channels
  per connection, and connections per address.
- **Different networks** with one set of users, settings and commands.

Start with one bot. Add more when one of these is a real problem: every bot
is something to keep updated, and networks frown on more bots than a channel
needs.

## Step 1: the simplest botnet, no links at all

Several copies of one bot in the same channel, and a rule that picks which
one answers:

> The bots know the team's nicks, in order. In a channel, a bot answers only
> if no team member before it in that order is in the channel.

Every bot sees the same channel, so they all agree without talking to each
other. When the first bot drops out, the next one answers by itself; when it
comes back, it takes over again.

`examples/botnet/teambot.py` is this in about 20 lines, and its scenario
tests the takeover and the handback:

```
python3 -m irclab run examples/botnet/teambot.scenario
```

The catch: when a bot's machine dies, the IRC server only notices after its
ping timeout (often a minute or more). Until then the dead bot still looks
present, and nobody answers. For many channels that is fine. When it is not,
link the bots.

## Step 2: linking the bots

### The shape: a hub and leaves

```
            hub  ─────────  althub (takes over when the hub is gone)
          /  |  \
     leaf  leaf  leaf      (each leaf links only to the hub)
```

- One **hub** knows the whole botnet: which bots exist, which channels each
  one is in, the shared settings. Leaves link to the hub only.
- An **althub** (alternate hub) is a leaf that takes over when it cannot
  reach the hub for a while (ghostbot waits a minute), and hands back when
  the hub returns. Without one, losing the hub's machine freezes changes.
- Put the hub and althub on **different machines**, and leaves on as many
  machines as you can: the point is that no single machine takes everything
  down.

A star like this is far simpler than letting every bot link to every other,
and it is what eggdrops do too (hubs and leaves, with `+h` and `+a` flags).

### The link itself

Any language can do this with what it has built in:

- **TLS over TCP**, with the leaf **pinning the hub's certificate
  fingerprint** (a SHA-256 of the certificate, copied into each leaf's
  config when it is set up). A self-made certificate is fine: the pinned
  fingerprint is what proves it is your hub, not a certificate authority.
  Take the fingerprint from the hub's own machine
  (`openssl x509 -in hub.crt -outform DER | sha256sum`), not from the first
  connection, or whoever sits in the middle at that moment gets pinned
  (see [security.md](security.md), point 6).
- *The format below is ghostkit's own, used by `examples/botnet/link/`.
  ghostbot's link works the same way but its messages differ (the hub
  speaks first, the MAC is over `kind|nonce|name`), so the two do not link
  to each other.*
- **One JSON object per line**, each with a type field: easy to read and
  write in every language, and easy to log while debugging.

  ```
  {"t": "hello", "name": "leaf1", "nonce": "8c1f4e2a9b3d7f60", "role": "leaf"}
  {"t": "chans", "bot": "leaf1", "chans": ["#test", "#mybots"]}
  {"t": "ping"}
  ```

- **Both sides prove they know a shared secret**, without sending it:
  1. The leaf connects and sends `hello` with its name and a random nonce.
  2. The hub sends back its own random nonce.
  3. The leaf answers with `HMAC-SHA256(secret, "leaf:" + hub_nonce + ":" + leaf_name)`
     and the hub checks it; the hub answers with
     `HMAC-SHA256(secret, "hub:" + leaf_nonce + ":" + leaf_name)` and the leaf
     checks that. Compare MACs with a constant-time function.

  Each side's nonce makes every login different, so a recorded login cannot
  be replayed. A fixed example to check an implementation against: secret
  `correct-horse-battery-staple`, leaf `leaf1`, hub nonce
  `8c1f4e2a9b3d7f60`, leaf nonce `3e9a0d5c71b24f86`:

  ```
  leaf's MAC = 464e8d3c350972b43cfee1b4b8cad5741b1157c3929dd3421e2be34040bf2e73
  hub's MAC  = 0e025a4aee37806b7c3d134940c25204226507bd74337e631b6110d0db608bb2

  printf %s 'leaf:8c1f4e2a9b3d7f60:leaf1' | openssl dgst -sha256 -hmac 'correct-horse-battery-staple'
  ```

- **Heartbeats.** Send a ping every 30 seconds and drop the link after 90 seconds
  of silence; a TCP connection can stay "open" long after the other side is
  gone.
- **Reconnect with growing waits** (5 s, 10 s, 20 s ... up to a few
  minutes), with some randomness, so a restarted hub is not hit by every
  leaf at the same moment.
- **Limit what a link can do.** A leaf obeys orders only from the hub, never
  from another leaf; the hub obeys no orders from leaves; the hub checks
  that a leaf only reports about itself, and decides itself which bots have
  special roles. Then one confused leaf cannot command the others.
- **Know what one shared secret means.** If every bot proves the same
  secret, a bot that holds it can log in under any name. The checks above
  stop mistakes and limit the damage; against a stolen secret, only a new
  secret on every bot helps. Per-bot secrets (the hub keeps one per name)
  bind a name to a secret, at the cost of more setup.
- **Harden the hub's port**, which is open to the internet:
  - the same answer for a wrong name and a wrong secret, a second later;
  - only a few logins waiting at once from one address (and in all), each
    with a time limit;
  - a nonce of a sensible length (16 to 128 characters) from the client;
  - lines no longer than 64 kB, and anything that is not a JSON object of
    the expected shape answered with an error, never a crash.
- **Mind the replacement trap.** When a leaf reconnects, its new link
  replaces the old one, which is then closed; the old link's cleanup must
  not clear the leaf's *new* connection, or the leaf looks linked but gets
  nothing. Clean up only if the closing link is still the current one.

### A worked example

`examples/botnet/link/` is all of this in Python, standard library only:
`botlink.py` (the hub, the leaf, the login, heartbeats, reconnecting, shared
data, the port's limits; about 500 lines, written to be ported) and
`linkbot.py`, an IRC bot that works alone and links when given a role:

```
python3 linkbot.py --name Solo --port 6667                       # alone
python3 linkbot.py --name Hub --role hub --link-port 7000 --cert-dir hubcert --secret-file my.secret
python3 linkbot.py --name Leaf1 --role leaf --link-port 7000 --hub-cert hubcert/link.crt \
    --secret-file my.secret --team Hub,Leaf1
```

Linked, the hub names one bot to answer in each channel, notes made with
`!remember` reach every bot, a leaf that loses the hub answers by the rule
of step 1, and a hub that comes back empty learns the notes from its
leaves. `linkbot.scenario` tests all of that with irclab;
`tests/test_botlink.py` tests the protocol against the example values above
and the port against the attacks below. A bot in another language can link
to `linkbot.py`'s hub if it speaks the same lines.

Test the port itself, not only the client: [security.md](security.md),
point 5, lists the cases (wrong and replayed logins, nonsense, huge lines,
many idle connections, a link replaced).

## Who answers in a channel

With links, the hub decides: it names one **main bot** per channel, and only
that bot answers commands there. When the main bot drops off, the hub names
another, at once rather than after a server's ping timeout. Keep the rule from
step 1 as the fallback for when a bot cannot reach the hub, so a channel is
never left with nobody answering.

## Sharing data between bots

Access lists, channel settings, who is in which channel: every bot needs the
same answer, even when links come and go.

- Give **every entry its own timestamp**, and let the newest change of each
  entry win when two bots disagree. Whole-table copies lose changes made
  while a link was down.
- **Remember deletions** for a while (ghostbot: 90 days) as entries marked
  deleted, or a bot that was offline brings the deleted entry back.
- A bot that joins with data of its own **merges** it rather than losing it.
- Keep the machines' clocks right (NTP): "newest wins" needs clocks that agree.
- **Send a whole table in pieces** when a bot links (ghostbot: about 512 kB
  each). Each piece is an ordinary update, merged entry by entry, so older
  bots take them as before, and no single message grows with the botnet.
- **Refuse timestamps far ahead** of your own clock (ghostbot: more than 10
  minutes) and anything that is not a real number: an entry dated next year
  wins over every change until then, whether a clock is wrong or a bot
  means harm. Log it, so the owner fixes the clock.
- **Settle ties the same way everywhere.** Two bots can change an entry at
  the same moment (two hubs during a split). If each keeps its own value
  on equal timestamps, they never agree again; compare the values
  themselves (for example their JSON with sorted keys) and keep the larger.
- Table names from a link are plain words, and a link cannot create
  unlimited tables (or files named after them).

Test it: give two copies the same changes in different orders, repeated,
and check they end equal.

## Placing bots in channels

- Spread each channel's bots over **different machines**.
- Where services can op a bot (Q, ChanServ), one bot may be enough; where
  they cannot, use two, so the bots can op each other.
- When a bot drops off, **wait a little before sending a stand-in**
  (ghostbot: 2 minutes), so a quick reconnect or a reboot doesn't shuffle
  bots around. When it comes back, the extra bot leaves again.
- Never more bots in a channel than it needs: channels and networks notice.

Bots in a channel can **op, invite and unban each other** and pass on a
channel key, so a channel survives even without services.

## Updating many bots

A botnet is many copies of the same code, so updates must be safe:

- **Sign your releases**, and have bots install only releases signed with
  your key. The bots hold only the public key, so a stolen bot cannot push
  code to the others. (eggkit does this for eggdrop scripts;
  [eggkit.md](eggkit.md).)
- **Roll out one machine at a time**, and stop if a machine's bots don't
  come back.
- **Never accept code or shell commands from a link.** A link that can run
  anything turns one stolen bot into all of them.

## The networks' rules matter more for a botnet

A botnet multiplies everything a single bot does, good and bad:

- **Connections per address.** QuakeNet allows 5 per IP address without a
  trust ([quakenet.md](quakenet.md)); other networks have their own
  limits. Count your own IRC client too.
- **Connect one after another.** After a reboot, bots that all connect at
  once get throttled or banned: let them take turns (ghostbot: 30 seconds
  apart per machine).
- **Flood limits are per connection**, but networks see the whole botnet. A
  command answered by ten bots is a flood.
- **Bans.** When a network bans a bot, wait, find out why, and fix it.
  Never use the other bots or addresses to get around a ban: that turns one
  banned bot into a banned botnet.
- **Ask first** if you plan many bots on one network: network staff can
  grant trusts and will tell you what is welcome.

## Watching it

Tell the owners when something changes:

- a bot drops off and stays away (wait a few minutes before saying so,
  so reconnects don't spam);
- a bot comes back after that;
- a bot is on an older version than the rest;
- a network is almost out of channel places or connections.

A status page helps too: [webstatus.md](webstatus.md) shows every bot,
written by the bots themselves.

## Mixing different bots

Bots in different languages can share one botnet if they share the link
format. Eggdrops have their own botnet protocol (`.link`, hubs and leaves,
userfile sharing with the `share` module); to bring eggdrops and other bots
together, a small gateway can sit on both sides. ghostbot's ghostgate does
this: it links to ghostbot's hub like a leaf, and to eggdrops like an
eggdrop.

## Testing a botnet

irclab runs several bots in one scenario: each `bot` line starts another
one, and `stopbot <n>` stops one as if its machine went down.

```
bot python3 mybot.py --port {port} --name hub
bot python3 mybot.py --port {port} --name leaf1
wait Leaf1 in #test
stopbot 1
wait Hub offline
# ... the althub takes over; the channel still has a bot that answers
```

Test the hard cases on your own machine before your users meet them: the hub
going down, a bot coming back, two bots answering at once, a bot that cannot
reach the hub.

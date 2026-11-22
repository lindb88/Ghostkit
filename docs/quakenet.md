# Bots on QuakeNet

What a bot needs to know to live on [QuakeNet](https://www.quakenet.org),
whatever language it is written in. The settings here are the ones ghostbot
runs with on QuakeNet every day. Points marked *(check)* could not be
confirmed from current public sources when this was written: look them up on
quakenet.org or ask in #help there before relying on them.

## Connecting

| | |
|---|---|
| Server | `irc.quakenet.org`, port 6667 |
| TLS | ghostbot connects without TLS *(check whether QuakeNet offers it now)* |
| Server software | snircd, based on ircu: **no CAP, no SASL** |
| Nick length | 15 (the server's `NICKLEN` in its 005 lines says so) |
| Channels per user | read `CHANLIMIT` (or `MAXCHANNELS`) from the 005 lines; don't hard-code it |

Because there is no SASL, a bot logs in to **Q** after connecting (below),
and joins its channels only after that.

**Sending.** Stay under the flood limit: ghostbot sends a burst of up to 4
lines, then one line every 2 seconds. A bot that sends faster gets
disconnected with "Excess Flood". Messaging many *different* people or
channels quickly can also be refused with numeric 439 ("target change too
fast"): slow down, and answer in the channel instead of privately where you
can.

**Reconnecting.** Wait before reconnecting, and longer each time (5 s,
10 s, 20 s ... up to a few minutes). If the server says you came back too
fast ("throttled"), wait at least 90 seconds. When several bots share a
machine, let them connect one after another (ghostbot uses 30 seconds
apart), not all at once after a reboot.

## Connections per address

Without special permission, QuakeNet allows **5 connections from one IP
address**. Your own IRC client on the same machine counts too. A machine with
both IPv4 and IPv6 can hold 5 on each. More than that needs a *trust* from
QuakeNet, which shell providers and larger projects apply for; ask in #help.
A bot that keeps reconnecting over the limit is refused, and repeated
attempts can get the address banned.

## Q: accounts and logging in

Q is QuakeNet's service bot. Messages to it go to `Q@CServe.quakenet.org`,
and its answers come as notices from `Q!TheQBot@CServe.quakenet.org`. **Only
believe notices from exactly that host**: anyone can take the nick Q on
another network, or try to fake its messages.

### An account for the bot

From the bot's connection, with its nick as the account name you want:

```
PRIVMSG Q@CServe.quakenet.org :HELLO bot-owner@example.com bot-owner@example.com
```

Q creates the account and emails the password. (The email address is given
twice on purpose.)

### Logging in with CHALLENGEAUTH

Never send the password itself. Q's challenge login proves you know it:

1. Send `PRIVMSG Q@CServe.quakenet.org :CHALLENGE`.
2. Q answers with a notice: `CHALLENGE <challenge> HMAC-MD5 HMAC-SHA-1 HMAC-SHA-256 LEGACY-MD5`.
3. Work out the response, with every value written in lower-case hex:
   - `passhash = SHA256(the first 10 characters of the password)`
   - `key = SHA256(lowercase(account) + ":" + passhash)`
   - `response = HMAC-SHA256(key, challenge)`, with the hex string `key` as the
     HMAC key
4. Send `PRIVMSG Q@CServe.quakenet.org :CHALLENGEAUTH <account> <response> HMAC-SHA-256`.
5. Q answers `You are now logged in as <account>.`, or that the username or
   password is incorrect.

`lowercase` is IRC's: on QuakeNet `[]\~` count as the upper case of `{}|^`.

**Check your code against this example.** Account `MyAccount`, password
`correcthorse`, challenge `f2a4c8b0e1d3a5c7f9e2b4d6a8c0e1f3`:

```
passhash = a8bdd15173560f7cc6ee4b4a8ca29543c5095462b6eb7e4590c3f080e35d6f1d
response = 5a22a183e2189e27b0c78a69b5315d04a0492796ad29c3f99dee288eb33b8781
```

The same with nothing but openssl:

```
PH=$(printf %s correcthor | openssl dgst -sha256 -r | cut -d' ' -f1)
KEY=$(printf %s "myaccount:$PH" | openssl dgst -sha256 -r | cut -d' ' -f1)
printf %s f2a4c8b0e1d3a5c7f9e2b4d6a8c0e1f3 | openssl dgst -sha256 -hmac "$KEY" -r
```

**Store the passhash, not the password.** The login only needs `passhash`,
so a bot's config file can hold that instead of the password. Someone who
reads the file can still log in as the bot, but cannot learn a password you
may have used elsewhere. (With irctools: `q_auth=('MyAccount', 'sha256:' +
q_password_hash(password))`.)

### Hide the host before joining

Once logged in, set user mode `+x`:

```
MODE <yournick> +x
```

The server answers with numeric 396, and the bot's host becomes
`<account>.users.quakenet.org`. **Join channels only after that**, or the
bot's real address shows in every channel it joins. If the login fails, a
careful bot stays out of its channels rather than show its address.

## Channels and Q

- **Ops from Q.** If a channel has Q, give the bot's account automatic ops
  there. Someone with master or owner access in the channel sends:

  ```
  PRIVMSG Q@CServe.quakenet.org :CHANLEV #channel BotAccount +ao
  ```

  `+o` is op access and `+a` makes Q op the bot when it joins.
- **Asking Q for help** (with that access): `OP #channel`, `INVITE #channel`
  (for invite-only channels), `UNBANME #channel` (when the bot was banned).
  Send them as messages to `Q@CServe.quakenet.org`.
- **Getting Q or S for a channel.** Channels request Q (or S, the spam
  scanner) from **R**, QuakeNet's request service, which grants them only
  when the channel meets its requirements *(check the current requirements
  and the exact command on quakenet.org)*.

## Eggdrops on QuakeNet

- In the eggdrop's config, set the network type to QuakeNet:
  `set net-type "QuakeNet"` *(check the exact value in your eggdrop version's
  example config)*.
- Log in to Q from a script on connect (with CHALLENGEAUTH, as above), set
  `+x`, and only then join channels: start with the channels set
  `+inactive`, and clear that once the hidden host is in place.
- eggkit's `eggkit-util.tcl` has flood-safe sending (`::eggkit::say`) and
  per-person limits (`::eggkit::allow`).

## Test it before going live

irclab has a QuakeNet mode: no CAP, Q with HELLO, CHALLENGE and
CHALLENGEAUTH, OP, INVITE, UNBANME, and `+x` hidden hosts.

```
server quakenet
account BotAccount secretpassword
chanlev #test BotAccount ao
# someone is in the channel first, so the bot is not opped just for being first
user Owner owner
Owner join #test
bot python3 mybot.py --port {port}
wait MyBot in #test
wait MyBot op #test
```

The test checks that the bot logs in, joins, and gets ops by asking Q
(`OP #test`: irclab's Q ops on request, not by itself on join), without
touching the real network. irctools' tests also check that the host is
hidden before the bot joins.

## Being a good bot

QuakeNet allows bots, and networks stay friendly to bots that behave:

- don't flood, and answer once (one bot per channel answers; see
  [botnet.md](botnet.md));
- don't join channels uninvited, and leave when asked;
- don't message people who did not talk to the bot first;
- when a bot is banned from the network, find out why; never use another
  bot or address to get around a ban.

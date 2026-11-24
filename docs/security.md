# Security for bot authors

A bot is a program that strangers talk to all day. Everything below comes
from holes found and closed in ghostbot, ghostgate and their eggdrops; each
point says what goes wrong, what to do in any language, and how to test it.

## 1. Text from IRC is never code, and never a second line

**What goes wrong.** A reply that contains a line break becomes two IRC
lines, the second one written by whoever typed the text:
`!echo hi\r\nPRIVMSG NickServ :DROP`. In Tcl, `eval`, `subst` or `expr` on user
text runs it. In any language, user text in a shell command runs as shell.

**What to do.**
- Remove CR, LF and NUL from everything you send (irctools' `build()` does;
  eggkit's `::eggkit::clean`).
- Split user text into words with a regular expression, never as a Tcl list
  (`::eggkit::words`); never pass it to `eval`, a shell, or a template.
- Run programs with a list of arguments, never through a shell.

**Test it.** In an irclab scenario, send the command with a line break or
`[exec ...]` in it and `expect` the reply as one line, with the text intact.

## 2. Addresses people post (SSRF)

**What goes wrong.** A bot that fetches links people post (`!title`, feeds,
short-link expansion) connects wherever the link says. `!title
http://127.0.0.1:8080/admin`, a link to the home router, or
`http://169.254.169.254/` (cloud servers' secrets) makes the bot fetch from
inside your own network and repeat what it found.

Checking the address first is not enough, for three reasons:
- **DNS rebinding.** The bot looks the name up, sees a public address, says
  yes; then the HTTP library looks the name up *again* to connect, and this
  time the name answers `127.0.0.1`. The check and the connection must use
  the same lookup.
- **Redirects.** A public page answers "302, go to http://192.168.1.1/".
  Every step of a redirect needs the same check.
- **Odd spellings.** `http://2130706433/`, `http://0x7f000001/` and
  `http://[::ffff:127.0.0.1]/` are all this machine.

**What to do.**
- Allow `http` and `https` only, and no `user:password@` in the address.
- Look the name up **once**, refuse if **any** answer is not public, and
  connect to exactly the addresses you checked; then do TLS with the
  original name. Do the same for every redirect, and allow at most a few.
- Not public: loopback (127/8, ::1), private (10/8, 172.16/12, 192.168/16,
  fc00::/7), carrier-grade NAT (100.64/10), link-local (169.254/16,
  fe80::/10), multicast, reserved, unspecified, and IPv4 written inside IPv6.
- Refuse redirects from https to plain http.
- Ignore proxy settings from the environment: a proxy connects for you,
  past every check.
- Cap the size (a few hundred kB for a title) and the time (10 seconds).
- Put the result on IRC as one clean line (point 1): page titles can hold
  line breaks too.

In Python, irctools' `safefetch` does all of this:

```python
from irctools import safefetch
title = safefetch.page_title(url)          # one clean line, or raises safefetch.FetchError
```

**Test it.** Point the fetcher at a local web server through a name that
answers a public address first and `127.0.0.1` the second time (replace the
lookup function in the test); a safe fetcher looks it up once, so nothing
reaches the local server. `tests/test_safefetch.py` does this, plus the odd
spellings and a redirect into the local network.

## 3. Login limits behind a proxy (X-Forwarded-For)

**What goes wrong.** A bot's web page behind Apache, nginx or relayd sees
every visitor as the proxy, so it reads the visitor's address from
`X-Forwarded-For`. But the visitor can send that header too, and a proxy
usually *adds* the real address at the end: `X-Forwarded-For: 1.2.3.4,
203.0.113.9`. A page that takes the first entry lets anyone claim a new
address on every try, and get around a "3 wrong passwords a minute" limit.

**What to do.**
- Believe `X-Forwarded-For` only when the connection comes from your own
  proxy (usually this machine), and take only the entry it added: the last
  one (with two proxies in a row, the second last, and only if the first
  one cannot be bypassed).
- Better still, have the proxy replace the header: Apache `RequestHeader
  unset X-Forwarded-For` (it then adds the real one), relayd `match request
  header set "X-Forwarded-For" value "$REMOTE_ADDR"`.
- Anything that is not an IP address counts as the proxy itself.
- Warn at start when the list of trusted proxies holds a public or very
  large network: everyone there can then choose their address.
- **Count the try before checking the password**, and take it back if the
  password was right. A good password hash is slow on purpose, so a page
  that counts only failures lets twenty logins sent at the same moment all
  pass the limit before the first failure is counted. Allow one check per
  username at a time, too.

**Test it.** Send three wrong logins with `X-Forwarded-For: <new made-up
address>, <the same real address>` and check that the fourth is refused.
Then send sixteen wrong logins at once and check that at most three get a
"wrong password" answer; the rest must be told to wait.

## 4. Logins on a bot's web page

**What goes wrong.** Someone's login is stolen (a shared computer, a copied
cookie), and changing the password does not end it. Or another web site
makes the visitor's browser send a request to the bot's page, with the
visitor's cookie (cross-site request forgery).

**What to do.**
- Changing a password ends every other login of that account, on the web
  and wherever else the bot keeps logins (on IRC too); the browser that
  changed it stays logged in.
- Show each login's start and when and from which address it was last used,
  and let the owner end any of them.
- The login cookie: `HttpOnly; Secure; SameSite=Strict`, and named with the
  `__Host-` prefix (`__Host-session`, with `Path=/` and no `Domain`): a
  browser then takes it only from your site itself over https, so another
  site under the same domain cannot plant a login of its own. Read only that
  name; an older name is ignored (and cleared at the next login or logout).
- Check on every request that the session's account still exists and work
  out its permissions afresh, so a removed account or a demotion applies at
  once, not when the session expires.
- Remove expired sessions when a new one is made, and keep at most a few per
  account (the oldest goes): otherwise logging in again and again fills the
  memory.
- Accept only `Content-Type: application/json` for anything that changes
  something, and only POST. A form on another site can send only
  form-encoded data or plain text, and a script on another site cannot send
  JSON without the browser asking your page first (which it should refuse:
  send no `Access-Control-Allow-*` headers). GET never changes anything.

**Test it.** Log in twice, change the password in one, and check the other
is refused. For every POST route, send a form-encoded and a plain-text body
with a valid cookie and check each is refused. Send the session under the
old name and check it is refused. Remove an account and check its session
ends on the next request.

**Behind a web server (FastCGI).** FastCGI believes what the web server
says (the visitor's address, the headers), so keep its port on 127.0.0.1
and warn at start when it is not. Its records also make it easy to hold
resources: answer one request at a time and refuse more than a few open
request IDs on one connection; limit connections; give each record and each
whole request a time limit (tens of seconds, not minutes). Test with request
IDs that are never finished, records that stop half way, and a few hundred
idle connections.

## 5. Links between your bots

docs/botnet.md describes a link: TLS with a pinned certificate, then a
login that proves a shared secret. The login port is open to the internet,
so it needs more than correct cryptography:

- **A fresh challenge every time.** The server sends a random nonce, the
  client proves the secret over it; a recorded login is then useless.
- **The same answer for a wrong name and a wrong secret**, and only after a
  second: no learning which names exist, no fast guessing.
- **Few logins waiting at once** (ghostgate: 4 per address, 64 in all), each
  with a time limit (15 seconds): otherwise idle connections fill the port
  and real bots cannot get in.
- **Limits on lines** (64 kB), and anything that is not the expected shape
  (not a JSON object, a name that is a list, a nonce of the wrong length)
  answered with an error, never an unhandled exception.
- **Limits after the login too.** A bot that logged in can still be stolen:
  cap each message (ghostbot: 1 MB, except the shared tables), and how much
  a link may send in a minute (ghostbot: 64 MB or 6,000 messages); more ends
  the link. Send big data (a whole table at link time) in pieces, so the
  cap can stay low as the data grows.
- **The name comes from the login, never from a message.** Whatever a
  message says about who sent it (a "bot" field in a note, a second "this is
  me" line), use the name the link logged in with; a link keeps its first
  name until it closes.
- **No plain links across a network.** Without TLS nothing proves who the
  other end is; allow plain TCP only to this machine (127.0.0.1, ::1).
- **Messages are data from outside, even after the login.** Accept only a
  JSON object; refuse `NaN` and `Infinity` (many JSON libraries accept
  them, and a `NaN` timestamp never compares as older); limit how deep it
  nests (check without recursion, or a deep nest overflows the checker).
  A message with a wrong type where a name or number belongs is logged
  and skipped, not an exception that ends the link.
- **Orders go one way.** Things only the hub may say (reload, reconnect,
  "hand back", an update) are obeyed only from the hub, never from a leaf,
  whichever bot the message claims to come from.
- **Roles are not claimed.** If some links may do more (a gateway passing
  on other bots' commands), the hub decides which names have that role; a
  link saying "I am the gateway" is not enough.
- **Shared data is power.** If bots share tables (access lists, password
  hashes), a write to such a table is as strong as the command that would
  have made it, so a stolen bot can grant itself anything the tables
  grant. Check entries for the shapes your own commands make (a channel's
  entry is never more than the channel's owner), and let only bots you
  name change what gives access to everything (admins, the owners'
  passwords). ghostbot 2.45 does both (`[link] rpc_from`).
- **Names are names.** Take only the characters a name can have (an IRC
  nick's), and refuse one that is another bot's written differently; a
  name with a newline writes fake lines in your log, and an invisible
  character makes a look-alike.
- **Size first, then parse.** Read at most a short line (a few MB) and look
  at its size before parsing it: a 15 MB line of small JSON arrays takes
  about 50 times that in memory while it is parsed.
- **Raise only the events you expect.** If links can raise events on the
  other side, keep a list of the ones a link really sends.
- **The replacement trap.** When a bot reconnects, the new connection
  replaces the old one, and the old one is closed. Its cleanup code then
  runs, and if it simply clears "this bot's connection", it clears the
  *new* one: the bot looks linked but gets nothing. Let a session clean up
  only if it is still the current one.

**Test it.** Open the real link port in a test and try each: a wrong
secret, a wrong name, an old login replayed, a protocol number you do not
speak, `[]`, `null`, a 100 kB line, ten idle connections, and two logins in
a row (the second must keep working). After logging in: `NaN`, a list
nested a thousand deep, a name that is a list, and a leaf sending an order
only the hub may give. ghostgate's `tests/test_ghostgate.py`
(class `LinkPort`) is a worked example.

## 6. Pinning certificates

**What goes wrong.** Bots linked over TLS with their own certificates check
each other by a pinned fingerprint. If a bot pins whatever it sees the
first time ("trust on first use"), someone in the middle at that moment
gets pinned instead.

**What to do.**
- Get the fingerprint from the other machine itself, not over the network:
  `openssl x509 -in cert.pem -outform DER | sha256sum` there; compare it with
  what your bot sees (`openssl s_client -connect host:port </dev/null |
  openssl x509 -outform DER | sha256sum`).
- When both ends are on one machine, read the certificate file directly at
  setup.
- If you do pin on first use, tell the owner which fingerprint was pinned,
  so it can still be checked, and offer a setting that never pins
  automatically.
- On a mismatch, refuse to link and tell the owner (once per certificate,
  not every retry); never repin by yourself.
- When you write the pin into a config file, change that one line: a
  config parser that writes the whole file back drops the owner's
  comments. And see "Secrets on disk" below.

## 7. Updates

**What goes wrong.** A bot that downloads new versions of itself runs
whatever the server hands it, so a hacked web server owns every bot.

**What to do.**
- Sign releases (Ed25519); bots keep only the public key and install
  nothing unsigned (eggkit's `eggkit-update.tcl`, `sign.py`).
- Refuse versions older than the one installed, and files signed for
  another program.
- Fetch over https only, and refuse redirects to plain http: the signature
  protects the contents, https keeps others from seeing or holding back
  what the bot fetches.
- Never accept code or shell commands over a bot link.
- Install carefully: write the download to a temporary file of its own,
  made new (`O_EXCL`, Tcl `{WRONLY CREAT EXCL}`) so it never goes through a
  link someone left there; give the new file the old one's permissions;
  update a symlinked script where it points; and on any failure (a full
  disk), remove the temporary file and say so, so the next check still
  runs.
- Refuse a public key of small order when it is configured: with one, a
  trivial signature passes for every file.

### Secrets on disk

A config file with secrets is written whole through a temporary file and a
rename. Make that temporary file private from the start (`mkstemp`, or
`os.open(..., O_CREAT | O_EXCL, 0o600)`), not with the umask and a chmod
afterwards: in between, another user on the machine (a shell host) can open
it, and an open file stays readable after the chmod.

**Test it.** Serve a release signed with another key, an older signed
release, and a different program's signed release; each must be refused,
and the installed version left as it was (eggkit's tests do this).

## 8. Flooding

**What goes wrong.** A command anyone can repeat fifty times a second gets
the bot disconnected for flooding, or makes it flood others.

**What to do.**
- Pace everything you send (a few lines at once, then one every 1.5 to 2
  seconds; irctools' `SendQueue`).
- Limit commands per person (ghostbot: 8 in 10 seconds; eggkit's
  `::eggkit::allow`), and answer in the channel rather than privately when
  many people ask.
- In a botnet, let one bot answer (docs/botnet.md).

**Test it.** In an irclab scenario, send the same command ten times and
`refute` more than the allowed number of answers.

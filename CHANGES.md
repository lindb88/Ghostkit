# ghostkit: version history

### 0.1.1 (2026-11-22)

- Examples in other languages, each tested with irclab: a Node.js bot and a
  Go bot (`examples/node`, `examples/go`), and a README section on testing a
  bot in any language. Their tests run where node and go are installed.
- irctools: QuakeNet's Q login (`Bot(q_auth=(account, password))`):
  CHALLENGEAUTH with HMAC-SHA-256, the hidden host (`+x`) before any channel
  is joined, only the real Q believed; `q_required=True` stays out of
  channels when the login fails. The echo bot has `--q-account` (the
  password from `$Q_PASSWORD`).
- irctools: `q_password_hash()`, so a bot can store only the hash Q's login
  needs (`q_auth=('Account', 'sha256:<hex>')`), not the password.
- irclab: several bots in one scenario (each `bot` line starts another),
  `stopbot <n>` to stop one as if its machine went down, and `wait <nick>
  offline` / `wait <nick> out <#chan>`. A bot that dies is named in the
  failure.
- Guides for any language: **docs/botnet.md** (from one bot to a linked
  botnet: who answers, links with a pinned certificate and a shared-secret
  login, shared data, placing bots, safe updates, the networks' rules) and
  **docs/quakenet.md** (connecting, the 5-per-address limit, Q accounts,
  CHALLENGEAUTH with a worked example, the hidden host, CHANLEV, eggdrops).
  Both have fixed examples to check an implementation against.
- examples/botnet/teambot.py: the simplest botnet, two bots where one
  answers and the other takes over when it goes, tested with irclab.
- irctools: `stop()` closes the connection itself when the server does not
  answer QUIT within 5 seconds, ends a reconnect wait at once, and
  `await bot.shutdown()` waits until the bot has finished; `run()` turns
  Ctrl-C and SIGTERM into a goodbye. `bot.reconnect_delay` sets the first
  wait.
- irctools: CAP negotiation waits for every requested capability to be
  answered, however many ACK lines the server uses, before SASL or `CAP
  END` (before, split ACKs could end negotiation before the SASL login);
  `CAP END` goes once; CAP DEL handled; a stalled negotiation ends after 30
  seconds.
- eggkit-update.tcl: https only by default; plain `http://` needs
  `-allowhttp 1`, and redirects may not leave https.
- webstatus: the status file's permissions can be set (`mode=`, default
  0644 so a web server can read it), with advice for shared folders.
- **webstatus**: a status page for a bot in any language. The bot writes
  `status.json`; one self-contained HTML file shows its bots, connection
  times, channels and user counts, warns when the file is old, and refreshes
  every 30 seconds. Read-only and safe to put online. `python3 -m webstatus
  serve | check | install | demo`. irctools' `Bot.status_file()` writes it;
  the Python, Node.js and Go examples have `--status`.

### 0.1.0 (2026-11-22)

The first release.

- **irclab**: a test IRC server (plain, Libera.Chat-like with NickServ,
  ChanServ and SASL, or QuakeNet-like with Q), scripted users, and
  scenario files that test a bot in any language (`python3 -m irclab run`).
  `python3 -m irclab serve` runs just the server.
- **irctools**: IRC line parsing and building (IRCv3 tags; text that cannot
  become a second line), case rules, masks, a flood-safe send queue, and a
  `Bot` with TLS, CAP, SASL, reconnects, channel tracking and commands.
- **eggkit**: `eggkit-util.tcl` (safe word splitting, cleaning, splitting to
  fit, flood limits), `eggkit-ed25519.tcl` (signature checks in plain Tcl,
  openssl when there), `eggkit-update.tcl` (signed self-updates from a web
  address: no unsigned files, no other scripts, no downgrades) and
  `sign.py` (keys and signatures).
- **ghostbot plugins**: a guide and a complete example plugin with its test,
  for ghostbot 2.29's plugin kit.
- Examples: an echo bot on irctools with its scenario, an eggdrop script on
  eggkit.

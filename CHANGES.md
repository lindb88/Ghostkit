# ghostkit: version history

### 0.1.1 (not released yet)

- Examples in other languages, each tested with irclab: a Node.js bot and a
  Go bot (`examples/node`, `examples/go`), and a README section on testing a
  bot in any language. Their tests run where node and go are installed.

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

# Security

## Reporting a problem

Please report security problems privately to lindb88 at
hello@lindb.net rather than in a public issue, with what you found and how
to reproduce it. You will get an
answer, and credit in CHANGES.md if you want it.

## What the tools protect against

- **irctools** removes CR, LF and NUL from everything it sends, so text from
  users cannot become IRC commands of its own; TLS is verified by default.
- **eggkit-update.tcl** installs a file only when its Ed25519 signature
  matches your public key, its `eggkit-name` is the script's own, and its
  `eggkit-version` is newer than the installed one. A hacked web server, a
  wrong DNS answer or a replayed old release cannot get code onto your bots.
- **eggkit-util.tcl** gives safe ways to split, clean and rate-limit user
  text.

## What they do not

- The **secret signing key** is the whole of the update security. Keep it
  off your bots and servers, readable only by you, with a backup offline.
  Whoever has it can push code to every install. If it leaks, make a new
  key and put the new public key on each bot by hand.
- **irclab** is a test server: no passwords are protected, anyone who can
  reach its port can connect. It listens on 127.0.0.1 unless told
  otherwise; keep it there.
- The Tcl signature check runs in your eggdrop's Tcl. It is the same math
  as openssl's (and tested against it), but it is not hardened against
  timing measurement; it only ever checks public data, so this does not
  matter for signatures.

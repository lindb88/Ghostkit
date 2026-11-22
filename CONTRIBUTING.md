# Contributing

Thank you for helping. A few things keep ghostkit easy to use:

- **No dependencies.** Python 3.8+ standard library, Tcl 8.6+ with no
  packages. If a change needs a package, it does not belong here.
- **Tests with every change.** `python3 tests/run.py` must pass. A bug fix
  comes with a test that failed before it. Tests run against the local test
  server, never a real network.
- **Plain words.** Docs and messages are for people who are good at their
  own thing, not necessarily at IRC internals: say what something does and
  why, in short sentences.
- **Safe by default.** Text from IRC is untrusted: it must never become a
  second IRC line, a shell command or Tcl code. Anything that installs or
  runs files checks a signature first.

Please open an issue before a large change, so we can agree on the shape
first.

By contributing you agree that your work is released under the MIT license
in LICENSE.

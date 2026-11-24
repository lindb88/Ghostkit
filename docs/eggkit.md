# eggkit: safer eggdrop scripts

Three Tcl scripts and a signing tool for people writing eggdrop scripts. Tcl
8.6 or 9; nothing to compile, no packages to install.

| File | |
|---|---|
| `eggkit/eggkit-util.tcl` | helpers that avoid the usual scripting mistakes |
| `eggkit/eggkit-ed25519.tcl` | checks Ed25519 signatures in plain Tcl |
| `eggkit/eggkit-update.tcl` | scripts that update themselves, only with files you signed |
| `eggkit/sign.py` | makes your signing key and signs your releases (Python 3.8+, on your own machine) |

Copy the `.tcl` files to your eggdrop's `scripts/` folder and add to its
config, in this order:

```tcl
source scripts/eggkit-ed25519.tcl
source scripts/eggkit-update.tcl
source scripts/eggkit-util.tcl
```

`examples/eggdrop/greeter.tcl` uses all three.

## The helpers

```tcl
::eggkit::words $text              ;# what a user typed, as a list of words, safely
::eggkit::clean $text              ;# CR, LF and NUL become spaces
::eggkit::strip $text              ;# without colors, bold, underline and the like
::eggkit::say $target $text        ;# PRIVMSG through the help queue: cleaned and split to fit
::eggkit::notice $target $text     ;# the same as a NOTICE
::eggkit::allow $key $seconds      ;# 1 at most once per $seconds per key, else 0
::eggkit::allow_n $key $n $secs    ;# 1 for up to $n times per $secs per key
::eggkit::split_bytes $text $max   ;# pieces of at most $max UTF-8 bytes, at spaces
```

`say` and `notice` use eggdrop's help queue, its slowest and most
flood-safe one: right after the bot joins a channel, while it is busy with
its own queries, a reply can take several seconds. For a reply that must be
quick, send it yourself with `putserv` and `::eggkit::clean`.

Why they exist:

- `[lindex $text 1]` on what a user typed treats it as a Tcl list: text with
  `{` or `"` in it errors out or splits wrong. `::eggkit::words` splits on
  spaces and nothing else.
- `eval`, `subst`, or `expr $x` without braces run text as Tcl. Never on
  anything a user typed: a user who types `[exec rm -rf ~]` gets it run.
- A reply that contains a line break goes out as two IRC lines, the second
  chosen by whoever typed the text: `PRIVMSG #chan :hi` then `QUIT`, or
  `PRIVMSG NickServ :DROP`. `say` and `notice` clean it.
- A public command anyone can repeat fifty times a second gets the bot
  disconnected for flooding. `allow` makes that one line:

```tcl
bind pub - !hello hello
proc hello {nick uhost hand chan text} {
  if {![::eggkit::allow "hello:$uhost" 10]} { return 0 }      ;# once per 10 s per person
  set who [lindex [::eggkit::words $text] 0]
  if {$who eq ""} { set who $nick }
  ::eggkit::say $chan "Hello, [::eggkit::strip $who]!"
  return 1
}
```

## Signed self-updates

A script that downloads new versions of itself must not run whatever the
server hands it: a hacked web server or a wrong DNS answer would otherwise
own every eggdrop running the script. With eggkit the eggdrop holds only your
**public** key and installs a file only when its signature matches.

### Once: make your key

```
python3 eggkit/sign.py keygen release.key
3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29
```

The printed line is the public key; it goes into your script. `release.key`
is the secret one: it stays on your own machine, readable only by you. Back
it up somewhere offline. Lose it and existing installs can no longer be
updated; leak it and anyone can push code to them.

### In your script

Two lines near the top name it and give its version, and one line registers
it:

```tcl
# eggkit-name: greeter
# eggkit-version: 1.0

::eggkit::update::register greeter [info script] \
    https://example.org/eggdrop/greeter.tcl 3b6a27bc...59da29 -daily 1
```

### Each release

```
# raise "# eggkit-version:" in greeter.tcl, then:
python3 eggkit/sign.py sign release.key greeter.tcl        # writes greeter.tcl.sig
# upload greeter.tcl and greeter.tcl.sig to the address
```

The eggdrops check once a day (with `-daily 1`), or at once when an owner
types `.eggupdate` (all scripts) or `.eggupdate greeter` on the partyline.

### What is checked

Before the old file is touched:

1. the signature (`<address>.sig`) is by your key;
2. the signed file names itself with the same `eggkit-name`, so a signed
   release of another of your scripts cannot be swapped in;
3. its `eggkit-version` is newer than the installed one, never older, so
   an old signed release with a known bug cannot be pushed back;
4. it is a sane size (1 byte to 2 MB; `-maxsize` to change) and arrived whole.

Then the old file is kept as `greeter.tcl.previous`, the new one is written
next to it and renamed into place (a crash halfway never leaves half a
script), and the bot rehashes. Since 0.1.4: the new file is written to a
temporary file of its own, made new and private (never through a file or
link already there), and gets the old file's permissions; a script that is
a symlink is updated where the link points (with `.previous` beside it);
and a failure on the way (a full disk, a missing folder) is logged and
reported as `failed`, the temporary file removed, and the next check runs
as usual. Every step is logged:

```
eggupdate: greeter: updated 1.0 -> 1.1 (signature checked by openssl); rehashing
eggupdate: greeter: refused: the signature does not match your key
```

The address must be **https://**. Plain `http://` is refused unless you add
`-allowhttp 1`: the signature would still stop a changed file, but anyone
on the way could see what the bot fetches and hold back new releases.
Redirects may not leave https either. Downloads use curl when it is
installed, else Tcl's `http` package (which needs the `tls` package for
https). They run in the background, so a slow server never freezes the bot,
and `-maxsize` holds while the data comes in: a server sending more is cut
off, not kept in memory.

**Prefer curl.** With Tcl's `http` and `tls` packages, eggkit has the
server's certificate checked against the machine's CA certificates
(`-require 1`), found in the usual places (Debian, Red Hat, OpenBSD,
FreeBSD, Alpine); set `::eggkit::update::cafile` to point elsewhere. If none
are found, it fetches nothing over https. Whether the `tls` package also
checks that the certificate is for the right host name depends on its
version; curl always does.

`register` options: `-daily 0|1`, `-maxsize <bytes>`, `-allowhttp 0|1`. From your own code:
`::eggkit::update::check greeter ?callback?`, where the callback gets the
name, `updated`, `current` or `failed`, and the message.

## Checking signatures yourself

```tcl
::eggkit::ed25519::verify_hex $pubhex $data $sighex     ;# 1 or 0
::eggkit::ed25519::verify $pub $data $sig               ;# raw bytes: 32, any, 64
::eggkit::ed25519::verify_file $pubhex $path $sighex    ;# "openssl", "tcl", or "" when it does not match
::eggkit::ed25519::sha512 $data                         ;# 64 raw bytes
```

`verify_file` asks openssl first (milliseconds) and falls back to the Tcl
check (about a second for 50 kB) when openssl is missing, older than 1.1.1,
or says no. Data is bytes: read files with `open $path rb`. Text holding
characters past `\xff` is taken as UTF-8.

The signatures are standard Ed25519 (RFC 8032): openssl, libsodium and
Python's `cryptography` make and check the same ones, so you can sign with
any of them. With openssl:

```
openssl genpkey -algorithm ed25519 -out release.pem
openssl pkey -in release.pem -pubout -outform DER | tail -c 32 | od -An -tx1 | tr -d ' \n'   # public key, hex
openssl pkeyutl -sign -inkey release.pem -rawin -in greeter.tcl | od -An -tx1 | tr -d ' \n' > greeter.tcl.sig
```

## Moving files over the botnet instead

If your eggdrops are linked and you would rather not use a web server, the
same check works on files sent over the botnet (`putbot` in pieces, then
`::eggkit::ed25519::verify_hex` before writing). ghostbot's `ghostgate.tcl`
does exactly that; read it for a worked example.

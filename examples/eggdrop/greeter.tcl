# eggkit-name: greeter
# eggkit-version: 1.0
#
# greeter.tcl: an example eggdrop script built on eggkit. It answers !hello and !about, is flood
# safe, and keeps itself up to date from a web address with signed releases.
#
# Install: copy eggkit/*.tcl and this file to your eggdrop's scripts/ folder, then in the config:
#   source scripts/eggkit-ed25519.tcl
#   source scripts/eggkit-update.tcl
#   source scripts/eggkit-util.tcl
#   source scripts/greeter.tcl
#
# Releasing a new version (on your own machine, never on the bot):
#   1. raise the eggkit-version line above
#   2. python3 eggkit/sign.py sign release.key greeter.tcl      -> greeter.tcl.sig
#   3. upload greeter.tcl and greeter.tcl.sig to the address below
# Bots check daily, or at once with .eggupdate greeter on the partyline.

namespace eval ::greeter {
  # your public key (python3 eggkit/sign.py keygen release.key prints it); this one is only an example
  variable key "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
  variable url "https://example.org/eggdrop/greeter.tcl"

  if {[info commands ::eggkit::update::register] ne ""} {
    ::eggkit::update::register greeter [info script] $url $key -daily 1
  }

  bind pub - !hello ::greeter::hello
  proc hello {nick uhost hand chan text} {
    # one answer per person per 10 seconds, and per channel at most 5 a minute
    if {![::eggkit::allow "hello:$uhost" 10] || ![::eggkit::allow_n "hello:$chan" 5 60]} { return 0 }
    set who [lindex [::eggkit::words $text] 0]
    if {$who eq ""} { set who $nick }
    ::eggkit::say $chan "Hello, [::eggkit::strip $who]!"
    return 1
  }

  bind pub - !about ::greeter::about
  proc about {nick uhost hand chan text} {
    if {![::eggkit::allow "about:$uhost" 30]} { return 0 }
    ::eggkit::notice $nick "greeter 1.0, an eggkit example (MIT license)."
    return 1
  }
}

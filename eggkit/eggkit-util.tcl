# eggkit-util.tcl: small helpers that avoid the usual mistakes in eggdrop scripts.
#
#   source scripts/eggkit-util.tcl
#
#   ::eggkit::words $text              user text as a list of words, safely (never [lindex $text 0] on raw text)
#   ::eggkit::clean $text              CR, LF and NUL become spaces: text that cannot become a second IRC line
#   ::eggkit::strip $text              without colors, bold, underline and the like
#   ::eggkit::say $target $text        PRIVMSG through the help queue, cleaned, split in pieces that fit
#   ::eggkit::notice $target $text     the same as a NOTICE
#   ::eggkit::allow $key $seconds      1 at most once per $seconds per key (flood protection), else 0
#   ::eggkit::allow_n $key $n $secs    1 for up to $n times per $secs per key
#   ::eggkit::split_bytes $text $max   pieces of at most $max UTF-8 bytes, at spaces where possible
#
# The usual mistakes, and why these exist:
#   - [lindex $text 1] on what a user typed fails or misbehaves on text with { or " in it: use words.
#   - [eval], [subst] or [expr $x] (no braces) on user text runs it as Tcl: never; use words and {*}.
#   - text with a line break goes out as two IRC lines, the second one chosen by whoever typed it: clean.
#   - a public command anyone can repeat 50 times a second gets the bot killed for flooding: allow.
# Part of ghostkit (MIT license).

namespace eval ::eggkit {
  variable util_version 0.1.0
  variable seen
  array set seen {}

  proc words {text} {
    return [regexp -all -inline {\S+} $text]
  }

  proc clean {text} {
    return [string map [list \r " " \n " " \0 " "] $text]
  }

  proc strip {text} {
    # \003 colors with their numbers, then bold, reset, reverse, italic, underline, strike, monospace
    regsub -all {\003(\d{1,2}(,\d{1,2})?)?} $text "" text
    regsub -all {\004[0-9a-fA-F]{0,6}(,[0-9a-fA-F]{6})?} $text "" text
    return [string map [list \002 "" \017 "" \026 "" \035 "" \037 "" \036 "" \021 ""] $text]
  }

  # pieces of at most max UTF-8 bytes, cut at a space where there is one, never inside a character
  proc split_bytes {text {max 400}} {
    set out {}
    set text [string trim [clean $text]]
    while {[string length [encoding convertto utf-8 $text]] > $max} {
      # the most characters that fit
      set lo 1; set hi [string length $text]
      while {$lo < $hi} {
        set mid [expr {($lo + $hi + 1) / 2}]
        if {[string length [encoding convertto utf-8 [string range $text 0 $mid-1]]] <= $max} {
          set lo $mid
        } else {
          set hi [expr {$mid - 1}]
        }
      }
      set cut [string last " " [string range $text 0 $lo]]
      if {$cut < $lo / 2} { set cut $lo }               ;# no space in the last half: cut the word
      lappend out [string trimright [string range $text 0 $cut-1]]
      set text [string trimleft [string range $text $cut end]]
    }
    if {$text ne ""} { lappend out $text }
    return $out
  }

  proc say {target text} {
    foreach piece [split_bytes $text 400] { puthelp "PRIVMSG $target :$piece" }
  }

  proc notice {target text} {
    foreach piece [split_bytes $text 400] { puthelp "NOTICE $target :$piece" }
  }

  proc allow {key seconds} {
    return [allow_n $key 1 $seconds]
  }

  proc allow_n {key n seconds} {
    variable seen
    set now [clock seconds]
    set keep {}
    if {[info exists seen($key)]} {
      foreach t $seen($key) { if {$now - $t < $seconds} { lappend keep $t } }
    }
    if {[llength $keep] >= $n} { set seen($key) $keep; return 0 }
    lappend keep $now
    set seen($key) $keep
    return 1
  }

  # forget old entries now and then, so the table does not grow forever
  proc tidy {args} {
    variable seen
    set now [clock seconds]
    foreach key [array names seen] {
      if {$now - [lindex $seen($key) end] > 3600} { unset seen($key) }
    }
  }
  if {[info commands ::bind] ne ""} { bind time - "*7 * * * *" ::eggkit::tidy }
}

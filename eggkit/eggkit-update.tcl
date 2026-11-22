# eggkit-update.tcl: lets your eggdrop scripts update themselves from a web address, only with files you signed.
#
#   source scripts/eggkit-ed25519.tcl
#   source scripts/eggkit-update.tcl
#   ::eggkit::update::register myscript scripts/myscript.tcl \
#       https://example.org/myscript.tcl <your public key hex> -daily 1
#
# Then, on the partyline (owners, +n):  .eggupdate             checks every registered script
#                                       .eggupdate myscript    checks one
# With -daily 1 it also checks once a day by itself.
#
# What it checks before anything is replaced:
#   - the signature (<url>.sig, made with "eggkit/sign.py sign") is by your key; nothing unsigned goes in
#   - the new file names itself:  # eggkit-name: myscript   so a signed file of another script cannot be swapped in
#   - its version line            # eggkit-version: 1.4     is newer than the installed one; never older, so an old
#                                 signed file with a known bug cannot be pushed back on you
#   - it is a sane size (1 byte to 2 MB by default) and arrived whole
# The old file is kept as <file>.previous, the new one is written next to it and renamed into place (a crash
# halfway never leaves half a script), and the bot rehashes.
#
# Fetching: https only (plain http:// only with -allowhttp 1); curl when installed, else Tcl's http package (which
# needs the tls package for https). Redirects may not leave https. Fetches run in
# the background; the bot never freezes waiting for a slow server.
# Part of ghostkit (MIT license).

namespace eval ::eggkit::update {
  variable version 0.1.0
  variable scripts                   ;# name -> dict: file url key daily maxsize
  variable busy                      ;# name -> 1 while a check runs
  array set scripts {}
  array set busy {}

  # a script to keep up to date. Options: -daily 0|1, -maxsize <bytes>, -allowhttp 0|1
  # The address must be https:// (or file:// for a local folder). Plain http:// needs -allowhttp 1: the signature
  # still stops a changed file, but anyone on the way sees what the bot fetches and can hold back new releases.
  proc register {name file url pubhex args} {
    variable scripts
    if {![regexp {^[0-9a-fA-F]{64}$} $pubhex]} { error "eggkit::update: $name: the key must be 64 hex characters" }
    set o [dict merge {-daily 0 -maxsize 2000000 -allowhttp 0} $args]
    set http [string is true -strict [dict get $o -allowhttp]]
    if {[string match -nocase http://* $url] && !$http} {
      error "eggkit::update: $name: the address is plain http://; use https://, or add -allowhttp 1 if you must"
    }
    if {![regexp -nocase {^https://} $url] && ![string match -nocase http://* $url] && \
        ![string match file://* $url]} {
      error "eggkit::update: $name: the address must start with https://"
    }
    set scripts($name) [dict create file $file url $url key [string tolower $pubhex] \
                          daily [dict get $o -daily] maxsize [dict get $o -maxsize] http $http]
    return $name
  }

  # the version a script file says it is, or "" when it has no "# eggkit-version:" line
  proc file_version {text} {
    if {[regexp -line {^#\s*eggkit-version:\s*([0-9][0-9.]*)\s*$} $text -> v]} { return $v }
    return ""
  }

  proc file_name {text} {
    if {[regexp -line {^#\s*eggkit-name:\s*(\S+)\s*$} $text -> n]} { return $n }
    return ""
  }

  # is version a newer than b ("1.10" > "1.9")?
  proc newer {a b} {
    if {$b eq ""} { return 1 }
    if {[catch {package vcompare $a $b} c]} { return 0 }
    return [expr {$c > 0}]
  }

  proc say {msg} {
    if {[info commands ::putlog] ne ""} { ::putlog "eggupdate: $msg" } else { puts "eggupdate: $msg" }
  }

  # check one script; done (optional) is called with: name result message (result: updated, current, failed)
  proc check {name {done ""}} {
    variable scripts; variable busy
    if {![info exists scripts($name)]} { error "no script called $name is registered" }
    if {[info exists busy($name)]} { return }
    set busy($name) 1
    set url [dict get $scripts($name) url]
    fetch $url [dict get $scripts($name) maxsize] [list [namespace current]::got_file $name $done] \
      [dict get $scripts($name) http]
  }

  proc got_file {name done ok data} {
    variable scripts
    if {!$ok} { return [finish $name $done failed "could not fetch it: $data"] }
    set url [dict get $scripts($name) url]
    fetch $url.sig 1000 [list [namespace current]::got_sig $name $done $data] [dict get $scripts($name) http]
  }

  proc got_sig {name done data ok sig} {
    variable scripts
    if {!$ok} { return [finish $name $done failed "could not fetch the signature: $sig"] }
    set why [install $name $data [string trim $sig]]
    if {[string match "updated *" $why]} {
      finish $name $done updated $why
      if {[info commands ::rehash] ne ""} { utimer 1 ::rehash }
    } elseif {[string match "current*" $why]} {
      finish $name $done current $why
    } else {
      finish $name $done failed $why
    }
  }

  proc finish {name done result msg} {
    variable busy
    unset -nocomplain busy($name)
    say "$name: $msg"
    if {$done ne ""} { {*}$done $name $result $msg }
  }

  # every check, then the replacement; "updated <version>", "current <version>", or why not
  proc install {name data sighex} {
    variable scripts
    set s $scripts($name)
    set file [dict get $s file]
    set size [string length $data]
    if {$size < 1 || $size > [dict get $s maxsize]} { return "refused: an unlikely size ($size bytes)" }
    if {![regexp {^[0-9a-fA-F]{128}$} $sighex]} { return "refused: no valid signature came with it" }
    # the signature first: nothing about an unsigned file is looked at further
    set tmp $file.eggkit-new
    set f [open $tmp wb]; puts -nonewline $f $data; close $f
    set via [::eggkit::ed25519::verify_file [dict get $s key] $tmp $sighex]
    if {$via eq ""} {
      file delete -force $tmp
      return "refused: the signature does not match your key"
    }
    set text [encoding convertfrom utf-8 $data]
    if {[file_name $text] ne $name} {
      file delete -force $tmp
      return "refused: the signed file is not $name (its eggkit-name line says \"[file_name $text]\")"
    }
    set new [file_version $text]
    if {$new eq ""} {
      file delete -force $tmp
      return "refused: the new file has no eggkit-version line"
    }
    set old ""
    if {[file exists $file]} {
      set f [open $file r]; fconfigure $f -encoding utf-8; set old [file_version [read $f]]; close $f
    }
    if {![newer $new $old]} {
      file delete -force $tmp
      return "current $old (the published file is $new)"
    }
    if {[file exists $file]} { file copy -force $file $file.previous }
    file rename -force $tmp $file
    return "updated $old -> $new (signature checked by $via); rehashing"
  }

  # fetch url in the background; then cmd is called with: ok data-or-error. http: plain http allowed
  proc fetch {url maxsize cmd {http 0}} {
    set curl [auto_execok curl]
    if {$curl ne ""} {
      # the address is one argument; --proto keeps curl to https (http only when allowed, file only for a
      # file:// address), and --proto-redir keeps a redirect from going anywhere else, such as https to http
      set protos https
      if {$http} { append protos ,http }
      set first $protos
      if {[string match file://* $url]} { append first ,file }
      if {[catch {open |[list {*}$curl -fsSL --proto =$first --proto-redir =$protos --max-time 60 \
                                       --max-filesize $maxsize -- $url 2>@1] rb} ch]} {
        return [after 0 [list {*}$cmd 0 $ch]]
      }
      fconfigure $ch -blocking 0 -translation binary
      set buf [namespace current]::buf$ch
      set $buf ""
      fileevent $ch readable [list [namespace current]::pipe_read $ch $buf $maxsize $cmd]
      return
    }
    if {[catch {package require http}]} { return [after 0 [list {*}$cmd 0 "neither curl nor Tcl's http package"]] }
    if {![string match -nocase https://* $url] && !($http && [string match -nocase http://* $url])} {
      return [after 0 [list {*}$cmd 0 "Tcl's http package fetches only https:// (or http:// with -allowhttp 1)"]]
    }
    if {[string match https://* $url]} {
      if {[catch {package require tls}]} { return [after 0 [list {*}$cmd 0 "https needs curl or the tls package"]] }
      ::http::register https 443 [list ::tls::socket -autoservername 1]
    }
    if {[catch {::http::geturl $url -binary 1 -timeout 60000 \
                  -command [list [namespace current]::http_done $maxsize $cmd]} e]} {
      after 0 [list {*}$cmd 0 $e]
    }
  }

  proc pipe_read {ch buf maxsize cmd} {
    upvar #0 $buf data
    append data [read $ch]
    if {[string length $data] > $maxsize + 1000} {
      catch {close $ch}
      unset data
      return [{*}$cmd 0 "it is larger than allowed"]
    }
    if {[eof $ch]} {
      fconfigure $ch -blocking 1
      set bad [catch {close $ch} err]
      set got $data
      unset data
      if {$bad} { return [{*}$cmd 0 [string range [regsub -all {\s+} [string trim "$got $err"] " "] 0 300]] }
      {*}$cmd 1 $got
    }
  }

  proc http_done {maxsize cmd tok} {
    set status [::http::status $tok]; set code [::http::ncode $tok]; set body [::http::data $tok]
    ::http::cleanup $tok
    if {$status ne "ok" || $code != 200} { return [{*}$cmd 0 "$status (HTTP $code)"] }
    if {[string length $body] > $maxsize} { return [{*}$cmd 0 "it is larger than allowed"] }
    {*}$cmd 1 $body
  }

  # ---- in an eggdrop: the partyline command and the daily check ----
  proc dcc {hand idx text} {
    variable scripts
    set names [expr {[string trim $text] eq "" ? [lsort [array names scripts]] : [list [string trim $text]]}]
    if {![llength $names]} { putdcc $idx "No scripts are registered for updates."; return 0 }
    foreach n $names {
      if {![info exists scripts($n)]} { putdcc $idx "No script called $n is registered."; continue }
      putdcc $idx "Checking $n..."
      check $n [list [namespace current]::told $idx]
    }
    return 1
  }

  proc told {idx name result msg} {
    if {[valididx $idx]} { putdcc $idx "$name: $msg" }
  }

  proc daily {args} {
    variable scripts
    foreach n [array names scripts] {
      if {[dict get $scripts($n) daily]} { check $n }
    }
  }

  if {[info commands ::bind] ne ""} {
    bind dcc n eggupdate [namespace current]::dcc
    bind time - "17 04 * * *" [namespace current]::daily    ;# 04:17 every day
  }
}

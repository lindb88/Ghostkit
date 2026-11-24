# eggkit-ed25519.tcl: checks Ed25519 signatures in Tcl alone (8.6 or 9), in an eggdrop or in tclsh.
#
# What it is for: a script that updates itself (or takes files from elsewhere) should only accept what you
# signed. Sign with eggkit/sign.py (or openssl); the eggdrop holds only the public key, so a stolen eggdrop
# cannot sign anything.
#
#   source scripts/eggkit-ed25519.tcl
#   ::eggkit::ed25519::verify_hex $pubhex $data $sighex     ;# 1 or 0
#   ::eggkit::ed25519::verify $pub $data $sig               ;# the same with raw bytes (32, any, 64)
#   ::eggkit::ed25519::verify_file $pubhex $path $sighex    ;# openssl when it can, else the Tcl check
#   ::eggkit::ed25519::sha512 $data                         ;# 64 raw bytes
#
# Data is bytes: read files with "open $path rb" (or fconfigure -translation binary). Text holding characters
# past \xff is taken as UTF-8.
#
# RFC 8032 PureEdDSA, verify only (the eggdrop never needs to sign). The Tcl check takes about a second for a
# 50 kB file; verify_file asks openssl first (milliseconds) and falls back to Tcl when openssl is missing, too
# old for Ed25519 (before 1.1.1), or says no. The same code has guarded ghostbot's ghostgate.tcl updates.
# Part of ghostkit (MIT license).

namespace eval ::eggkit::ed25519 {
  variable version 0.1.0
  variable K {
    0x428a2f98d728ae22 0x7137449123ef65cd 0xb5c0fbcfec4d3b2f 0xe9b5dba58189dbbc 0x3956c25bf348b538
    0x59f111f1b605d019 0x923f82a4af194f9b 0xab1c5ed5da6d8118 0xd807aa98a3030242 0x12835b0145706fbe
    0x243185be4ee4b28c 0x550c7dc3d5ffb4e2 0x72be5d74f27b896f 0x80deb1fe3b1696b1 0x9bdc06a725c71235
    0xc19bf174cf692694 0xe49b69c19ef14ad2 0xefbe4786384f25e3 0x0fc19dc68b8cd5b5 0x240ca1cc77ac9c65
    0x2de92c6f592b0275 0x4a7484aa6ea6e483 0x5cb0a9dcbd41fbd4 0x76f988da831153b5 0x983e5152ee66dfab
    0xa831c66d2db43210 0xb00327c898fb213f 0xbf597fc7beef0ee4 0xc6e00bf33da88fc2 0xd5a79147930aa725
    0x06ca6351e003826f 0x142929670a0e6e70 0x27b70a8546d22ffc 0x2e1b21385c26c926 0x4d2c6dfc5ac42aed
    0x53380d139d95b3df 0x650a73548baf63de 0x766a0abb3c77b2a8 0x81c2c92e47edaee6 0x92722c851482353b
    0xa2bfe8a14cf10364 0xa81a664bbc423001 0xc24b8b70d0f89791 0xc76c51a30654be30 0xd192e819d6ef5218
    0xd69906245565a910 0xf40e35855771202a 0x106aa07032bbd1b8 0x19a4c116b8d2d0c8 0x1e376c085141ab53
    0x2748774cdf8eeb99 0x34b0bcb5e19b48a8 0x391c0cb3c5c95a63 0x4ed8aa4ae3418acb 0x5b9cca4f7763e373
    0x682e6ff3d6b2b8a3 0x748f82ee5defb2fc 0x78a5636f43172f60 0x84c87814a1f0ab72 0x8cc702081a6439ec
    0x90befffa23631e28 0xa4506cebde82bde9 0xbef9a3f7b2c67915 0xc67178f2e372532b 0xca273eceea26619c
    0xd186b8c721c0c207 0xeada7dd6cde0eb1e 0xf57d4f7fee6ed178 0x06f067aa72176fba 0x0a637dc5a2c898a6
    0x113f9804bef90dae 0x1b710b35131c471b 0x28db77f523047d84 0x32caab7b40c72493 0x3c9ebe0a15c9bebc
    0x431d67c49c100d4c 0x4cc5d4becb3e42b6 0x597f299cfc657e2a 0x5fcb6fab3ad6faec 0x6c44198c4a475817
  }
  variable p [expr {2**255 - 19}]
  variable L [expr {2**252 + 27742317777372353535851937790883648493}]
  variable d 37095705934669439343138083508754565189542113879843219016388785533085940283555
  variable i 19681161376707505956807079304988542015446066515923890162744021073123829784752
  variable B [list 15112221349535400772501151409588531511454012693041857206046113283949847762202 \
    46316835694926478169428394003475163141307993866256225615783033603165251855960 1 [expr {
    15112221349535400772501151409588531511454012693041857206046113283949847762202 *
    46316835694926478169428394003475163141307993866256225615783033603165251855960 % $p}]]

  proc sha512 {data} {
    variable K
    set M 0xFFFFFFFFFFFFFFFF
    set data [bytes $data]
    set len [string length $data]
    append data \x80 [string repeat \0 [expr {(239 - $len % 128) % 128}]] [binary format WW 0 [expr {$len * 8}]]
    lassign {0x6a09e667f3bcc908 0xbb67ae8584caa73b 0x3c6ef372fe94f82b 0xa54ff53a5f1d36f1
             0x510e527fade682d1 0x9b05688c2b3e6c1f 0x1f83d9abfb41bd6b 0x5be0cd19137e2179} h0 h1 h2 h3 h4 h5 h6 h7
    set n [string length $data]
    for {set off 0} {$off < $n} {incr off 128} {
      binary scan $data @${off}Wu16 w
      for {set t 16} {$t < 80} {incr t} {
        set x [lindex $w [expr {$t - 15}]]; set y [lindex $w [expr {$t - 2}]]
        lappend w [expr {([lindex $w [expr {$t - 16}]] + [lindex $w [expr {$t - 7}]] +
          ((($x >> 1) | ($x << 63)) ^ (($x >> 8) | ($x << 56)) ^ ($x >> 7)) +
          ((($y >> 19) | ($y << 45)) ^ (($y >> 61) | ($y << 3)) ^ ($y >> 6))) & $M}]
      }
      set a $h0; set b $h1; set c $h2; set dd $h3; set e $h4; set f $h5; set g $h6; set h $h7
      for {set t 0} {$t < 80} {incr t} {
        set t1 [expr {($h + (((($e >> 14) | ($e << 50)) ^ (($e >> 18) | ($e << 46)) ^ (($e >> 41) | ($e << 23))) & $M) +
          (($e & $f) ^ (($e ^ $M) & $g)) + [lindex $K $t] + [lindex $w $t]) & $M}]
        set t2 [expr {(((($a >> 28) | ($a << 36)) ^ (($a >> 34) | ($a << 30)) ^ (($a >> 39) | ($a << 25))) & $M) +
          (($a & $b) ^ ($a & $c) ^ ($b & $c))}]
        set h $g; set g $f; set f $e; set e [expr {($dd + $t1) & $M}]
        set dd $c; set c $b; set b $a; set a [expr {($t1 + $t2) & $M}]
      }
      set h0 [expr {($h0 + $a) & $M}]; set h1 [expr {($h1 + $b) & $M}]; set h2 [expr {($h2 + $c) & $M}]
      set h3 [expr {($h3 + $dd) & $M}]; set h4 [expr {($h4 + $e) & $M}]; set h5 [expr {($h5 + $f) & $M}]
      set h6 [expr {($h6 + $g) & $M}]; set h7 [expr {($h7 + $h) & $M}]
    }
    return [binary format W8 [list $h0 $h1 $h2 $h3 $h4 $h5 $h6 $h7]]
  }

  # data read from a file in binary (rb) is bytes already; text with characters past 255 is taken as UTF-8
  proc bytes {data} {
    if {[regexp {[^\x00-\xff]} $data]} { return [encoding convertto utf-8 $data] }
    return $data
  }

  # a little-endian number from bytes
  proc le {bytes} {
    binary scan [string reverse $bytes] H* hex
    return [expr {"0x$hex" + 0}]
  }

  proc modpow {b e m} {
    set r 1; set b [expr {$b % $m}]
    while {$e > 0} {
      if {$e & 1} { set r [expr {$r * $b % $m}] }
      set e [expr {$e >> 1}]; set b [expr {$b * $b % $m}]
    }
    return $r
  }

  # points in extended coordinates (X Y Z T); one formula adds and doubles
  proc padd {P Q} {
    variable p; variable d
    lassign $P x1 y1 z1 t1; lassign $Q x2 y2 z2 t2
    set A [expr {($y1 - $x1) * ($y2 - $x2) % $p}]
    set Bb [expr {($y1 + $x1) * ($y2 + $x2) % $p}]
    set C [expr {2 * $t1 * $t2 * $d % $p}]
    set D [expr {2 * $z1 * $z2 % $p}]
    set E [expr {$Bb - $A}]; set F [expr {$D - $C}]; set G [expr {$D + $C}]; set H [expr {$Bb + $A}]
    return [list [expr {$E * $F % $p}] [expr {$G * $H % $p}] [expr {$F * $G % $p}] [expr {$E * $H % $p}]]
  }

  proc pmul {s P} {
    set Q {0 1 1 0}
    while {$s > 0} {
      if {$s & 1} { set Q [padd $Q $P] }
      set P [padd $P $P]
      set s [expr {$s >> 1}]
    }
    return $Q
  }

  # 32 bytes to a point, or "" when they are not one
  proc pdecode {bytes} {
    variable p; variable d; variable i
    set y [le $bytes]
    set sign [expr {$y >> 255}]
    set y [expr {$y & (2**255 - 1)}]
    if {$y >= $p} { return "" }
    set yy [expr {$y * $y % $p}]
    set x2 [expr {($yy - 1) * [modpow [expr {$d * $yy + 1}] [expr {$p - 2}] $p] % $p}]
    if {$x2 == 0} {
      if {$sign} { return "" }
      return [list 0 $y 1 0]
    }
    set x [modpow $x2 [expr {($p + 3) / 8}] $p]
    if {($x * $x - $x2) % $p != 0} { set x [expr {$x * $i % $p}] }
    if {($x * $x - $x2) % $p != 0} { return "" }
    if {($x & 1) != $sign} { set x [expr {$p - $x}] }
    return [list $x $y 1 [expr {$x * $y % $p}]]
  }

  proc pencode {P} {
    variable p
    lassign $P x y z
    set zi [modpow $z [expr {$p - 2}] $p]
    set x [expr {$x * $zi % $p}]; set y [expr {$y * $zi % $p}]
    set n [expr {$y | (($x & 1) << 255)}]
    return [string reverse [binary decode hex [format %064llx $n]]]
  }

  # 0.1.4: 1 when the 32-byte key is no use as a public key: not a point, or one of the eight of small order (with
  # those, a trivial signature passes for any file; a real key is never one)
  proc weak_key {pub} {
    if {[string length $pub] != 32} { return 1 }
    set A [pdecode $pub]
    if {$A eq ""} { return 1 }
    lassign [pmul 8 $A] x y z
    variable p
    return [expr {$x % $p == 0 && ($y - $z) % $p == 0}]
  }

  # 1 when sig (64 bytes) was made by the owner of public key pub (32 bytes) over msg (bytes)
  proc verify {pub msg sig} {
    variable L; variable p; variable B
    set msg [bytes $msg]
    if {[string length $pub] != 32 || [string length $sig] != 64} { return 0 }
    set A [pdecode $pub]
    if {$A eq ""} { return 0 }
    set Rb [string range $sig 0 31]
    set s [le [string range $sig 32 63]]
    if {$s >= $L} { return 0 }
    set k [expr {[le [sha512 $Rb$pub$msg]] % $L}]
    lassign $A x y z t
    set Q [padd [pmul $s $B] [pmul $k [list [expr {$p - $x}] $y $z [expr {$p - $t}]]]]
    return [expr {[pencode $Q] eq $Rb}]
  }

  # the same, with the key and the signature in hex (64 and 128 characters)
  proc verify_hex {pubhex msg sighex} {
    if {![regexp {^[0-9a-fA-F]{64}$} $pubhex] || ![regexp {^[0-9a-fA-F]{128}$} [string trim $sighex]]} { return 0 }
    return [verify [binary decode hex $pubhex] $msg [binary decode hex [string trim $sighex]]]
  }

  # check a file: openssl when it can, the Tcl check otherwise. Returns "openssl", "tcl", or "" (not signed by key)
  proc verify_file {pubhex path sighex} {
    if {![regexp {^[0-9a-fA-F]{64}$} $pubhex] || ![regexp {^[0-9a-fA-F]{128}$} [string trim $sighex]]} { return "" }
    set f [open $path rb]; set data [read $f]; close $f
    set ossl [auto_execok openssl]
    if {$ossl ne ""} {
      set tmp [file join [file dirname [file normalize $path]] ".eggkit-[pid]-[clock clicks]"]
      set der [binary decode hex "302a300506032b6570032100$pubhex"]   ;# an Ed25519 public key in DER
      set ok 0
      if {![catch {
        set f [open $tmp.sig wb]; puts -nonewline $f [binary decode hex [string trim $sighex]]; close $f
        set f [open $tmp.pem w]
        puts $f "-----BEGIN PUBLIC KEY-----\n[binary encode base64 $der]\n-----END PUBLIC KEY-----"
        close $f
        set out [exec {*}$ossl pkeyutl -verify -pubin -inkey $tmp.pem -rawin -in $path -sigfile $tmp.sig 2>@1]
      }] && [string match "*Verified Successfully*" $out]} { set ok 1 }
      file delete -force $tmp.sig $tmp.pem
      if {$ok} { return openssl }
    }
    if {[verify_hex $pubhex $data $sighex]} { return tcl }
    return ""
  }

  namespace export verify verify_hex verify_file sha512 weak_key
}

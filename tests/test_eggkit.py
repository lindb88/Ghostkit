"""eggkit: the Tcl Ed25519 check against Python's and openssl's, the signed updater, and the helpers.

Needs a tclsh (8.6 or 9): TCLSH=/path/to/tclsh, else tclsh9.0, tclsh8.6 or tclsh on the PATH; skipped otherwise.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
EGG = os.path.join(ROOT, 'eggkit')
sys.path.insert(0, EGG)
import sign  # noqa: E402

TCLSH = os.environ.get('TCLSH') or next(
    (p for p in (shutil.which(n) for n in ('tclsh9.0', 'tclsh8.6', 'tclsh')) if p), None)
OPENSSL = shutil.which('openssl')
CURL = shutil.which('curl')

# RFC 8032, section 7.1: tests 1, 2, 3 and SHA(abc)
L = 2 ** 252 + 27742317777372353535851937790883648493
VECTORS = [
    ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
     'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
     'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
    ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
     '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
     '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ('c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7',
     'fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025', 'af82',
     '6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a'),
    ('833fe62409237b9d62ec77587520911e9a759cec1d19755b7da901b96dca3d42',
     'ec172b93ad5e563bf4932c70e1245034c35467ef2efd4d64ebf819683467e2bf',
     'ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f',
     'dc2a4459e7369633a52b1bf277839a00201009a3efbf3ecb69bea2186c26b58909351fc9ac90b3ecfdfbc7c66431e0303dca179c138ac17ad9bef1177331a704'),
]


def malleated(sig_hex):
    """The same signature with S + L: the same point equation, but not the one valid encoding (RFC 8032 5.1.7)."""
    sig = bytes.fromhex(sig_hex)
    return (sig[:32] + (int.from_bytes(sig[32:], 'little') + L).to_bytes(32, 'little')).hex()


def tcl(script, path_env=None, cwd=None):
    """Run Tcl with the eggkit scripts loaded (and eggdrop's putlog/utimer/rehash stubbed); its stdout."""
    head = ('proc putlog {m} { puts stderr "LOG: $m" }\n'
            'proc utimer {s cmd} { after [expr {$s*1000}] $cmd }\n'
            'proc rehash {} { set ::rehashed 1 }\n'
            'source {%s}\nsource {%s}\nsource {%s}\n') % (
        os.path.join(EGG, 'eggkit-ed25519.tcl'), os.path.join(EGG, 'eggkit-update.tcl'),
        os.path.join(EGG, 'eggkit-util.tcl'))
    env = dict(os.environ)
    if path_env is not None:
        env['PATH'] = path_env
    r = subprocess.run([TCLSH], input=head + script, capture_output=True, text=True, timeout=120, env=env, cwd=cwd)
    if r.returncode:
        raise AssertionError('tclsh failed:\n' + r.stdout + r.stderr)
    return r.stdout.strip()


class TestSignPy(unittest.TestCase):
    def test_rfc8032_vectors(self):
        for sk, pk, msg, sig in VECTORS:
            secret = bytes.fromhex(sk)
            self.assertEqual(sign.public_key(secret).hex(), pk)
            self.assertEqual(sign.sign(secret, bytes.fromhex(msg)).hex(), sig)
            self.assertTrue(sign.verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig)))
            self.assertFalse(sign.verify(bytes.fromhex(pk), b'x' + bytes.fromhex(msg), bytes.fromhex(sig)))
            self.assertFalse(sign.verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(malleated(sig))))
            self.assertFalse(sign.verify(bytes.fromhex(pk), bytes.fromhex(msg), bytes.fromhex(sig) + b'\0'))

    @unittest.skipUnless(OPENSSL, 'no openssl')
    def test_openssl_agrees(self):
        secret = os.urandom(32)
        with tempfile.TemporaryDirectory() as d:
            der = bytes.fromhex('302e020100300506032b657004220420') + secret
            with open(os.path.join(d, 'k.der'), 'wb') as f:
                f.write(der)
            with open(os.path.join(d, 'm'), 'wb') as f:
                f.write(b'some file contents\n' * 50)
            subprocess.run([OPENSSL, 'pkeyutl', '-sign', '-keyform', 'DER', '-inkey', 'k.der', '-rawin', '-in', 'm',
                            '-out', 'm.sig'], cwd=d, check=True, capture_output=True)
            with open(os.path.join(d, 'm.sig'), 'rb') as f:
                theirs = f.read()
        self.assertEqual(sign.sign(secret, b'some file contents\n' * 50), theirs)

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run([sys.executable, os.path.join(EGG, 'sign.py')] + list(a), cwd=d,
                                            capture_output=True, text=True)
            r = run('keygen', 'rel.key')
            self.assertEqual(r.returncode, 0, r.stderr)
            pub = r.stdout.strip()
            self.assertEqual(len(pub), 64)
            self.assertEqual(os.stat(os.path.join(d, 'rel.key')).st_mode & 0o777, 0o600)
            self.assertNotEqual(run('keygen', 'rel.key').returncode, 0)        # never overwrites a key
            self.assertEqual(run('public', 'rel.key').stdout.strip(), pub)
            with open(os.path.join(d, 's.tcl'), 'w') as f:
                f.write('puts hi\n')
            self.assertEqual(run('sign', 'rel.key', 's.tcl').returncode, 0)
            self.assertEqual(run('verify', pub, 's.tcl').returncode, 0)
            with open(os.path.join(d, 's.tcl'), 'a') as f:
                f.write('exec rm -rf ~\n')
            self.assertNotEqual(run('verify', pub, 's.tcl').returncode, 0)


@unittest.skipUnless(TCLSH, 'no tclsh')
class TestTclEd25519(unittest.TestCase):
    def test_sha512(self):
        cases = [b'', b'abc', b'a' * 111, b'a' * 112, b'a' * 127, b'a' * 128, bytes(range(256)) * 5]
        script = ''.join('binary scan [::eggkit::ed25519::sha512 [binary decode hex %s]] H* h; puts $h\n' % c.hex()
                         if c else 'binary scan [::eggkit::ed25519::sha512 ""] H* h; puts $h\n' for c in cases)
        self.assertEqual(tcl(script).split(), [hashlib.sha512(c).hexdigest() for c in cases])

    def test_vectors_and_tampering(self):
        lines = []
        for sk, pk, msg, sig in VECTORS:
            m = '[binary decode hex %s]' % msg if msg else '""'
            bad_sig = sig[:-2] + ('00' if sig[-2:] != '00' else '01')
            lines += ['puts [::eggkit::ed25519::verify_hex %s %s %s]' % (pk, m, sig),
                      'puts [::eggkit::ed25519::verify_hex %s "x" %s]' % (pk, sig),
                      'puts [::eggkit::ed25519::verify_hex %s %s %s]' % (pk, m, bad_sig),
                      'puts [::eggkit::ed25519::verify_hex nothex %s %s]' % (m, sig)]
            lines.append('puts [::eggkit::ed25519::verify_hex %s %s %s]' % (pk, m, malleated(sig)))
        self.assertEqual(tcl('\n'.join(lines)).split(), ['1', '0', '0', '0', '0'] * len(VECTORS))

    def test_python_signs_tcl_checks(self):
        secret = os.urandom(32)
        pub = sign.public_key(secret).hex()
        text = 'set x "åäö ☃"\n'                                  # characters past 255: taken as UTF-8
        sig = sign.sign(secret, text.encode()).hex()
        out = tcl('puts [::eggkit::ed25519::verify_hex %s {%s} %s]' % (pub, text, sig))
        self.assertEqual(out, '1')

    def test_verify_file_both_ways(self):
        secret = os.urandom(32)
        pub = sign.public_key(secret).hex()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'f.tcl')
            data = os.urandom(3000)
            with open(path, 'wb') as f:
                f.write(data)
            sig = sign.sign(secret, data).hex()
            script = 'puts [::eggkit::ed25519::verify_file %s {%s} %s]\n' % (pub, path, sig)
            script += 'puts "<[::eggkit::ed25519::verify_file %s {%s} %s]>"\n' % (pub, path, '00' * 64)
            bin_dir = os.path.dirname(TCLSH)
            no_openssl = tcl(script, path_env=bin_dir if not os.path.exists(os.path.join(bin_dir, 'openssl'))
                             else '/nonexistent').split()
            self.assertEqual(no_openssl, ['tcl', '<>'])
            if OPENSSL:
                self.assertEqual(tcl(script).split(), ['openssl', '<>'])
            self.assertEqual(os.listdir(d), ['f.tcl'])                 # no temporary files left behind


@unittest.skipUnless(TCLSH and CURL, 'needs tclsh and curl')
class TestUpdater(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.secret = os.urandom(32)
        self.pub = sign.public_key(self.secret).hex()
        self.installed = os.path.join(self.d, 'bot', 'myscript.tcl')
        self.pubdir = os.path.join(self.d, 'web')
        os.makedirs(os.path.dirname(self.installed))
        os.makedirs(self.pubdir)
        with open(self.installed, 'w') as f:
            f.write('# eggkit-name: myscript\n# eggkit-version: 1.9\nputs old\n')

    def tearDown(self):
        shutil.rmtree(self.d)

    def publish(self, text, name='myscript.tcl', signer=None, sig=None):
        path = os.path.join(self.pubdir, name)
        with open(path, 'w') as f:
            f.write(text)
        with open(path + '.sig', 'w') as f:
            f.write(sig or sign.sign(signer or self.secret, text.encode()).hex())

    def check(self):
        url = 'file://' + os.path.join(self.pubdir, 'myscript.tcl')
        out = tcl('::eggkit::update::register myscript {%s} {%s} %s\n'
                  'proc done {n r m} { puts "$r|$m"; set ::fin 1 }\n'
                  '::eggkit::update::check myscript done\n'
                  'vwait ::fin\nupdate\nafter 1200 {set ::w 1}; vwait ::w\n'
                  'puts "rehashed=[info exists ::rehashed]"\n' % (self.installed, url, self.pub))
        result, rehashed = out.splitlines()[-2:]
        with open(self.installed) as f:
            return result, rehashed == 'rehashed=1', f.read()

    def test_newer_signed_version_installs(self):
        new = '# eggkit-name: myscript\n# eggkit-version: 1.10\nputs new\n'
        self.publish(new)
        result, rehashed, now = self.check()
        self.assertTrue(result.startswith('updated|updated 1.9 -> 1.10'), result)
        self.assertTrue(rehashed)
        self.assertEqual(now, new)
        with open(self.installed + '.previous') as f:
            self.assertIn('puts old', f.read())

    def test_same_or_older_version_is_left(self):
        for v in ('1.9', '1.2'):
            self.publish('# eggkit-name: myscript\n# eggkit-version: %s\nputs other\n' % v)
            result, rehashed, now = self.check()
            self.assertTrue(result.startswith('current|'), result)
            self.assertFalse(rehashed)
            self.assertIn('puts old', now)

    def test_refusals_leave_the_script_alone(self):
        good = '# eggkit-name: myscript\n# eggkit-version: 2.0\nputs evil\n'
        cases = [
            (dict(text=good, signer=os.urandom(32)), 'does not match your key'),
            (dict(text=good, sig='zz' * 64), 'no valid signature'),
            (dict(text='# eggkit-name: otherscript\n# eggkit-version: 9.0\nputs x\n'), 'is not myscript'),
            (dict(text='# eggkit-name: myscript\nputs x\n'), 'no eggkit-version'),
        ]
        for kw, why in cases:
            self.publish(**kw)
            result, rehashed, now = self.check()
            self.assertTrue(result.startswith('failed|') and why in result, (why, result))
            self.assertFalse(rehashed)
            self.assertIn('puts old', now)
            self.assertEqual(sorted(os.listdir(os.path.dirname(self.installed))), ['myscript.tcl'])

    def test_plain_http_needs_opting_in(self):
        out = tcl('puts [catch {::eggkit::update::register a f http://example.org/a.tcl %s} e]\nputs $e\n'
                  'puts [catch {::eggkit::update::register b f ftp://example.org/b.tcl %s} e]\n'
                  'puts [catch {::eggkit::update::register c f http://example.org/c.tcl %s -allowhttp 1} e]\n'
                  'puts [catch {::eggkit::update::register d f HTTPS://example.org/d.tcl %s} e]\n'
                  % ((self.pub,) * 4))
        lines = out.splitlines()
        self.assertEqual(lines[0], '1')
        self.assertIn('-allowhttp 1', lines[1])
        self.assertEqual(lines[2:], ['1', '0', '0'])

    def test_http_when_allowed(self):
        import http.server
        import threading
        new = '# eggkit-name: myscript\n# eggkit-version: 2.0\nputs new\n'
        self.publish(new)
        handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=self.pubdir, **kw)  # noqa
        http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = 'http://127.0.0.1:%d/myscript.tcl' % srv.server_address[1]
            out = tcl('::eggkit::update::register myscript {%s} {%s} %s -allowhttp 1\n'
                      'proc done {n r m} { puts "$r|$m"; set ::fin 1 }\n'
                      '::eggkit::update::check myscript done\nvwait ::fin\n' % (self.installed, url, self.pub))
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(out.splitlines()[-1].startswith('updated|updated 1.9 -> 2.0'), out)
        # without the opt-in, curl itself refuses plain http (the same switch stops a redirect from https to http)
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = tcl('proc got {ok data} { puts "$ok|$data"; set ::fin 1 }\n'
                      '::eggkit::update::fetch http://127.0.0.1:%d/myscript.tcl 100000 got 0\nvwait ::fin\n'
                      % srv.server_address[1])
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertTrue(out.startswith('0|'), out)
        self.assertNotIn('puts new', out)

    def test_missing_file(self):
        result, rehashed, now = self.check()                          # nothing published
        self.assertTrue(result.startswith('failed|could not fetch it'), result)


@unittest.skipUnless(TCLSH, 'no tclsh')
class TestTclFetch(unittest.TestCase):
    """0.1.3: Tcl's http package (no curl): the size limit holds while the data comes in, and https checks the
    server's certificate against this machine's CA certificates."""

    NO_CURL = 'proc auto_execok {name} { return "" }\n'      # as on a machine without curl

    def serve_big(self):
        import http.server
        import threading

        class Big(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path.endswith('/withlen'):
                    self.send_header('Content-Length', str(30 * 1024 * 1024))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    for _ in range(480):                      # 30 MB
                        self.wfile.write(b'x' * 65536)
                except OSError:
                    pass

            def log_message(self, *a):
                pass
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Big)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_port

    def test_the_limit_holds_while_receiving(self):
        port = self.serve_big()
        script = self.NO_CURL + (
            'rename ::eggkit::update::http_done ::eggkit::update::http_done_orig\n'
            'proc ::eggkit::update::http_done {maxsize cmd tok} {\n'
            '  puts "held [string length [::http::data $tok]]"\n'
            '  ::eggkit::update::http_done_orig $maxsize $cmd $tok\n'
            '}\n'
            'proc done {ok data} { puts "ok=$ok $data"; set ::finished 1 }\n'
            'foreach p {/nolen /withlen} {\n'
            '  ::eggkit::update::fetch http://127.0.0.1:%d$p 2000000 done 1\n'
            '  vwait finished\n'
            '}\n' % port)
        out = tcl(script).splitlines()
        held = [int(l.split()[1]) for l in out if l.startswith('held')]
        self.assertEqual([l for l in out if l.startswith('ok=')], ['ok=0 it is larger than allowed'] * 2, out)
        self.assertTrue(all(h <= 2000000 + 65536 for h in held), held)       # before: all 30 MB

    def fake_tls(self, d):
        """A stand-in tls package that records how it is asked to connect (the real one is not needed)."""
        os.makedirs(os.path.join(d, 'tls'))
        with open(os.path.join(d, 'tls', 'pkgIndex.tcl'), 'w') as f:
            f.write('package ifneeded tls 9.9 [list source [file join $dir tls.tcl]]\n')
        with open(os.path.join(d, 'tls', 'tls.tcl'), 'w') as f:
            f.write('namespace eval ::tls {}\n'
                    'proc ::tls::socket {args} { set ::tls_args $args; error "fake tls" }\n'
                    'package provide tls 9.9\n')

    def test_https_checks_the_certificate(self):
        with tempfile.TemporaryDirectory() as d:
            self.fake_tls(d)
            ca = os.path.join(d, 'ca.pem')
            open(ca, 'w').write('not used by the fake\n')
            script = self.NO_CURL + (
                'lappend auto_path {%s}\n'
                'set ::eggkit::update::cafile {%s}\n'
                'proc done {ok data} { set ::finished 1 }\n'
                '::eggkit::update::fetch https://127.0.0.1:1/x 1000 done\n'
                'vwait finished\n'
                'puts $::tls_args\n'
                'set ::eggkit::update::cafile ""; set ::eggkit::update::cafiles {/no/such/file}\n'
                'proc done2 {ok data} { puts "ok=$ok $data"; set ::finished 1 }\n'
                '::eggkit::update::fetch https://127.0.0.1:1/x 1000 done2\n'
                'vwait finished\n' % (d, ca))
            out = tcl(script).splitlines()
            args = out[0].split()
            self.assertIn('-require', args)
            self.assertEqual(args[args.index('-require') + 1], '1')
            self.assertEqual(args[args.index('-cafile') + 1], ca)
            self.assertEqual(out[1], 'ok=0 no CA certificates found for the tls package '
                                     '(set ::eggkit::update::cafile, or install curl)')


@unittest.skipUnless(TCLSH, 'no tclsh')
class TestUtil(unittest.TestCase):
    def test_words_clean_strip(self):
        out = tcl('set t "  a \\{b \\"c  d"\nputs [llength [::eggkit::words $t]]\n'
                  'puts [::eggkit::clean "x\\r\\nQUIT :y"]\n'
                  'puts [::eggkit::strip "\\00304,01red\\003 \\002bold\\002 \\037u\\037"]\n')
        self.assertEqual(out.splitlines(), ['4', 'x  QUIT :y', 'red bold u'])

    def test_split_bytes(self):
        out = tcl('foreach p [::eggkit::split_bytes [string repeat "ord å " 100] 50] {\n'
                  '  puts "[string length [encoding convertto utf-8 $p]] $p"\n}\n')
        lines = out.splitlines()
        self.assertTrue(all(int(l.split(' ', 1)[0]) <= 50 for l in lines), lines)
        self.assertEqual(' '.join(l.split(' ', 1)[1] for l in lines).split(), ['ord', 'å'] * 100)

    def test_allow(self):
        out = tcl('puts [list [::eggkit::allow k 10] [::eggkit::allow k 10] [::eggkit::allow other 10]]\n'
                  'puts [list [::eggkit::allow_n c 3 60] [::eggkit::allow_n c 3 60] [::eggkit::allow_n c 3 60] '
                  '[::eggkit::allow_n c 3 60]]\n')
        self.assertEqual(out.splitlines(), ['1 0 1', '1 1 1 0'])

    def test_example_script_loads(self):
        out = tcl('proc bind {args} { lappend ::binds [lrange $args 0 2] }\n'
                  'proc puthelp {line} { puts $line }\n'
                  'source {%s}\n'
                  'puts [lsort $::binds]\n'
                  '::greeter::hello Bob bob@h * #c {}\n'
                  '::greeter::hello Bob bob@h * #c {}\n'
                  '::greeter::hello Ann ann@h * #c "\\00304Joe\\003 more"\n'
                  'puts [lsort [array names ::eggkit::update::scripts]]\n'
                  % os.path.join(ROOT, 'examples', 'eggdrop', 'greeter.tcl'))
        lines = out.splitlines()
        self.assertEqual(lines[0], '{pub - !about} {pub - !hello}')
        self.assertEqual(lines[1:], ['PRIVMSG #c :Hello, Bob!', 'PRIVMSG #c :Hello, Joe!', 'greeter'])


if __name__ == '__main__':
    unittest.main()

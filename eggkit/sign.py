#!/usr/bin/env python3
"""eggkit/sign.py: Ed25519 keys and signatures for scripts you ship, in Python 3.8+ with nothing else.

    python3 sign.py keygen release.key          # a new secret key (kept by you, never on a bot); prints the
                                                # public key to put in your script
    python3 sign.py public release.key          # the public key again
    python3 sign.py sign release.key myscript.tcl          # writes myscript.tcl.sig (128 hex characters)
    python3 sign.py verify <public hex> myscript.tcl [sig]  # checks it, as the eggdrop will

The secret key file holds 32 random bytes in hex, and is written readable by you only. Keep a copy somewhere
safe and offline: lose it and you can no longer ship updates that existing installs accept; leak it and anyone
can. The signatures are standard Ed25519 (RFC 8032): openssl, libsodium, and eggkit-ed25519.tcl all check them.
Part of ghostkit (MIT license).
"""

import hashlib
import os
import sys

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)


def _inv(x):
    return pow(x, P - 2, P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    DD = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, DD - C, DD + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _mul(s, pt):
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r


def _eq(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


def _x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * _inv(D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    return P - x if (x & 1) != sign else x


GY = 4 * _inv(5) % P
GX = _x(GY, 0)
G = (GX, GY, 1, GX * GY % P)


def _encode(pt):
    zi = _inv(pt[2])
    x, y = pt[0] * zi % P, pt[1] * zi % P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _decode(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, 'little')
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _expand(secret):
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], 'little') & ((1 << 254) - 8) | (1 << 254)
    return a, h[32:]


def _h(b):
    return int.from_bytes(hashlib.sha512(b).digest(), 'little') % Q


def public_key(secret):
    return _encode(_mul(_expand(secret)[0], G))


def sign(secret, msg):
    a, prefix = _expand(secret)
    A = _encode(_mul(a, G))
    r = _h(prefix + msg)
    R = _encode(_mul(r, G))
    return R + int.to_bytes((r + _h(R + A + msg) * a) % Q, 32, 'little')


def verify(public, msg, sig):
    if len(public) != 32 or len(sig) != 64:
        return False
    A, R = _decode(public), _decode(sig[:32])
    s = int.from_bytes(sig[32:], 'little')
    if A is None or R is None or s >= Q:
        return False
    return _eq(_mul(s, G), _add(R, _mul(_h(sig[:32] + public + msg), A)))


def read_secret(path):
    with open(path) as f:
        text = f.read().strip()
    try:
        secret = bytes.fromhex(text)
    except ValueError:
        secret = b''
    if len(secret) != 32:
        sys.exit('%s is not a secret key from "sign.py keygen"' % path)
    return secret


def main(argv):
    if len(argv) < 2 or argv[0] not in ('keygen', 'public', 'sign', 'verify'):
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == 'keygen':
        path = argv[1]
        if os.path.exists(path):
            sys.exit('%s already exists; not overwriting a key' % path)
        secret = os.urandom(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(secret.hex() + '\n')
        print(public_key(secret).hex())
        print('The secret key is in %s. Back it up offline; never put it on a bot.' % path, file=sys.stderr)
    elif cmd == 'public':
        print(public_key(read_secret(argv[1])).hex())
    elif cmd == 'sign':
        if len(argv) < 3:
            sys.exit('sign.py sign <key file> <file> [more files]')
        secret = read_secret(argv[1])
        for path in argv[2:]:
            with open(path, 'rb') as f:
                sig = sign(secret, f.read())
            with open(path + '.sig', 'w') as f:
                f.write(sig.hex() + '\n')
            print('%s.sig' % path)
    elif cmd == 'verify':
        if len(argv) < 3:
            sys.exit('sign.py verify <public hex> <file> [signature file]')
        try:
            pub = bytes.fromhex(argv[1])
        except ValueError:
            pub = b''
        with open(argv[2], 'rb') as f:
            data = f.read()
        with open(argv[3] if len(argv) > 3 else argv[2] + '.sig') as f:
            try:
                sig = bytes.fromhex(f.read().strip())
            except ValueError:
                sig = b''
        if verify(pub, data, sig):
            print('good signature')
        else:
            sys.exit('BAD signature: not signed by that key, or the file changed')


if __name__ == '__main__':
    main(sys.argv[1:])

#!/usr/bin/env python3
"""tools/bundle_irclab.py <out.py> [header line ...]: irclab as one file (server, client, scenario), for projects
that ship a single module (ghostbot's plugins/_irclab.py). The pieces keep their code; the imports between them go.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, '..', 'irclab')
PARTS = ('server.py', 'client.py', 'scenario.py')


def split(text):
    """(docstring, stdlib import lines, body) of a module."""
    m = re.match(r'\s*("""[\s\S]*?""")\n', text)
    doc = m.group(1) if m else ''
    rest = text[m.end():] if m else text
    imports, body = [], []
    for line in rest.split('\n'):
        if re.match(r'^(import \w|from \w[\w.]* import )', line) and not body:
            imports.append(line)
        elif re.match(r'^from \.', line):
            continue
        elif not body and not line.strip():
            continue
        else:
            body.append(line)
    return doc, imports, '\n'.join(body).rstrip() + '\n'


def main(out, header):
    version = re.search(r"__version__ = '([^']+)'", open(os.path.join(PKG, '__init__.py')).read()).group(1)
    docs, imports, bodies = [], set(), []
    for p in PARTS:
        doc, imp, body = split(open(os.path.join(PKG, p), encoding='utf-8').read())
        docs.append(doc)
        imports.update(imp)
        bodies.append('# ---- %s ' % p + '-' * (110 - len(p)) + '\n' + body)
    head = ''.join('# %s\n' % h for h in header)
    text = (head + '"""irclab %s in one file (made by ghostkit\'s tools/bundle_irclab.py; edit irclab, not this).\n\n'
            % version + docs[2].strip('"').strip() + '\n"""\n\n' + '\n'.join(sorted(imports)) + '\n\n__version__ = %r\n\n\n'
            % version + '\n\n'.join(bodies))
    compile(text, out, 'exec')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(text)
    print('%s: irclab %s, %d lines' % (out, version, text.count('\n')))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2:])

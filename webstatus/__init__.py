"""webstatus: a status page for IRC bots in any language.

The bot writes status.json (format below); index.html, next to it on any web server, shows it and refreshes
every 30 seconds. The page only reads: it has no login and changes nothing, so it is safe to put online.

    {
      "format": 1,
      "updated": 1795375799,                 # when the file was written (Unix seconds); the page warns when
                                             # it is over 5 minutes old (the bot stopped writing it)
      "title": "My bots",                    # optional: the page's heading
      "bots": [
        {
          "name": "HelloBot",
          "connected": true,
          "network": "Libera.Chat",          # optional from here on
          "server": "irc.libera.chat",
          "since": 1795370000,               # connected since (Unix seconds)
          "started": 1795360000,             # the program started
          "version": "1.2",
          "note": "maintenance at 22:00",    # a line of text for visitors
          "channels": [{"name": "#test", "users": 12, "op": true}]
        }
      ]
    }

From Python:

    from webstatus import write_status, check
    write_status('www/status.json', [{'name': 'HelloBot', 'connected': True, 'channels': [...]}])
    problems = check(json.load(open('www/status.json')))      # [] when it is in the format

Other languages write the same JSON (examples/node, examples/go). Write it to a temporary file and rename it
into place, so the page never reads half a file. Leave out channels people should not see (secret ones).
See docs/webstatus.md. Part of ghostkit (MIT license).
"""

import json
import os
import tempfile
import time

__version__ = '0.1.0'
FORMAT = 1
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')


def _int(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(data):
    """What is wrong with a status (the parsed JSON): a list of problems, [] when it is fine."""
    p = []
    if not isinstance(data, dict):
        return ['the file must hold a JSON object']
    if data.get('format', FORMAT) != FORMAT:
        p.append('"format" must be %d' % FORMAT)
    if 'updated' not in data:
        p.append('"updated" is missing (Unix seconds): without it the page cannot tell an old status')
    elif not _int(data['updated']):
        p.append('"updated" must be a number (Unix seconds)')
    elif data['updated'] > 1e11:
        p.append('"updated" looks like milliseconds; it must be seconds')
    if 'title' in data and not isinstance(data['title'], str):
        p.append('"title" must be text')
    bots = data.get('bots')
    if not isinstance(bots, list):
        return p + ['"bots" must be a list (one entry per bot)']
    for i, b in enumerate(bots):
        where = 'bots[%d]' % i
        if not isinstance(b, dict):
            p.append('%s must be an object' % where)
            continue
        if not isinstance(b.get('name'), str) or not b['name']:
            p.append('%s needs a "name"' % where)
        if not isinstance(b.get('connected'), bool):
            p.append('%s needs "connected": true or false' % where)
        for k in ('network', 'server', 'version', 'note'):
            if k in b and not isinstance(b[k], str):
                p.append('%s.%s must be text' % (where, k))
        for k in ('since', 'started'):
            if k in b and not (_int(b[k]) and b[k] < 1e11):
                p.append('%s.%s must be Unix seconds' % (where, k))
        chans = b.get('channels', [])
        if not isinstance(chans, list):
            p.append('%s.channels must be a list' % where)
            continue
        for j, c in enumerate(chans):
            cw = '%s.channels[%d]' % (where, j)
            if not isinstance(c, dict) or not isinstance(c.get('name'), str) or not c['name']:
                p.append('%s needs a "name"' % cw)
                continue
            if 'users' in c and not (_int(c['users']) and c['users'] >= 0):
                p.append('%s.users must be a number' % cw)
            if 'op' in c and not isinstance(c['op'], bool):
                p.append('%s.op must be true or false' % cw)
    return p


def write_status(path, bots, title=None, now=None, mode=0o644):
    """Write status.json atomically (a temporary file renamed into place).

    mode: its permissions. 0o644 (the default) lets a web server running as another user read it, which a public
    status page needs. If the folder is somewhere other users of the machine can look and the page is not meant
    for them, use 0o640 with a group the web server is in, or 0o600 when it runs as your own user."""
    data = {'format': FORMAT, 'updated': int(now or time.time()), 'bots': list(bots)}
    if title:
        data['title'] = title
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.status-', suffix='.json', dir=folder)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return data

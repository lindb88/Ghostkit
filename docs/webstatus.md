# webstatus: a status page for your bot

A web page that shows your bots: connected or not, for how long, which
channels and how many people are in them. It works for a bot in **any
language**: the bot only writes a small file, `status.json`, and the page
(one self-contained HTML file) reads it and refreshes every 30 seconds.

The page only reads. It has no login, changes nothing, and loads nothing from
elsewhere, so it is safe to put on a public web server.

```
python3 -m webstatus demo            # see it with made-up bots: http://127.0.0.1:8080/
```

## Three steps

1. **Your bot writes `status.json`** (the format is below), every 30 seconds
   or when something changes. The three example bots do it:

   ```
   python3 examples/echobot.py --status www/status.json
   node examples/node/hellobot.js --status www/status.json
   go run examples/go/hellobot.go -status www/status.json
   ```

2. **Check the file:** `python3 -m webstatus check www/status.json`.

3. **Show it:**
   - to look at it yourself: `python3 -m webstatus serve www` and open
     http://127.0.0.1:8080/
   - on your web server: `python3 -m webstatus install /var/www/bots` copies
     `index.html` there; have the bot write `status.json` into the same
     folder. Any web server that serves files works (nginx, Apache, Caddy,
     GitHub Pages with a bot that pushes the file).

`index.html?src=other.json` reads another file, for one page per bot.

## The format

```json
{
  "format": 1,
  "updated": 1795375799,
  "title": "My bots",
  "bots": [
    {
      "name": "HelloBot",
      "connected": true,
      "network": "Libera.Chat",
      "server": "irc.libera.chat",
      "since": 1795370000,
      "started": 1795360000,
      "version": "1.2",
      "note": "maintenance at 22:00",
      "channels": [{"name": "#test", "users": 12, "op": true}]
    }
  ]
}
```

| Field | | |
|---|---|---|
| `format` | `1` | |
| `updated` | when the file was written, in Unix **seconds** | required; the page warns when it is over 5 minutes old |
| `title` | the page's heading | optional |
| `bots` | a list, one entry per bot | required (a single bot is a list of one) |
| `name`, `connected` | the bot's nick; `true` or `false` | required |
| `network`, `server`, `version` | text | optional |
| `since` | connected since (Unix seconds) | optional; the page shows "connected for 3 days 5 hours" |
| `started` | the program started (Unix seconds) | optional; shown while not connected |
| `note` | a line for visitors | optional, up to 300 characters shown |
| `channels` | `name`, `users` (a number), `op` (`true` when the bot has ops) | optional |

## Writing it safely

- **Write a temporary file and rename it into place.** A rename is atomic,
  so the page never reads half a file. All three examples do this; from
  Python, `webstatus.write_status(path, bots)` does it for you.
- **Leave out secret channels.** Everything in the file is public once it is
  on a web server. With irctools: `bot.status_file('www/status.json',
  hide=['#secret'])`.
- **Mind who can read the file.** It is written readable by everyone on the
  machine (`0644`), because the web server usually runs as another user and
  the page is meant to be public. If it sits in a shared folder and holds
  things not everyone on the machine should see (server names, channel
  names), give it tighter permissions: `mode=0o640` with a group the web
  server is in, or `0o600` when the web server runs as your own user
  (`write_status(..., mode=...)`, `bot.status_file(..., mode=...)`).
- **Counts, not names.** The format holds how many people are in a channel,
  never who. Keep it that way: a list of nicks on a public page is personal
  data.
- **Seconds, not milliseconds.** `check` catches the mix-up
  (JavaScript's `Date.now()` is milliseconds: divide by 1000).

## With irctools

```python
bot = Bot('MyBot', 'irc.libera.chat', 6697, tls=True, channels=['#mytest'])
bot.status_file('www/status.json', every=30, title='My bots',
                network='Libera.Chat', version='1.0', hide=['#secret'])
bot.run()
```

It writes the file every 30 seconds while the bot runs, when the connection
drops, and once more when the bot stops (as not connected).

## Safety of the page itself

- Every value from the file is put into the page as text, never as HTML: a
  bot or channel name with `<script>` in it shows those characters and runs
  nothing. The tests check this in a real browser.
- A Content-Security-Policy in the page allows no outside scripts, styles,
  images or connections.
- `python3 -m webstatus serve` listens on 127.0.0.1 only, unless you pass
  `--host`, and serves just the page and `.json` files from one folder: no
  folder listings, no other files, nothing above the folder.

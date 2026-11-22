// A small IRC bot in Node.js, with nothing but Node's own modules. Test it with irclab:
//   python3 -m irclab run examples/node/hellobot.scenario
// Run it for real:  node hellobot.js --server irc.libera.chat --port 6667 --channel '#mytest'
// With a status page: add --status www/status.json, then: python3 -m webstatus serve www
'use strict';
const net = require('net');
const fs = require('fs');
const path = require('path');

const opt = { server: '127.0.0.1', port: 6667, nick: 'HelloBot', channel: '#test', status: '' };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i += 2) opt[argv[i].replace(/^--/, '')] = argv[i + 1];

const sock = net.connect(Number(opt.port), opt.server);
// never let text from IRC become a second line: no CR, LF or NUL in anything sent
const send = (line) => sock.write(line.replace(/[\r\n\0]/g, ' ') + '\r\n');
const say = (target, text) => send(`PRIVMSG ${target} :${text}`);

sock.on('connect', () => {
  send(`NICK ${opt.nick}`);
  send(`USER ${opt.nick.toLowerCase()} 0 * :hellobot in Node.js`);
});

let buf = '';
sock.on('data', (chunk) => {
  buf += chunk.toString('utf8');
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).replace(/\r$/, '');
    buf = buf.slice(i + 1);
    handle(line);
  }
});
sock.on('close', () => { connected = false; writeStatus(); process.exit(0); });

// ---- the status page (webstatus): who is where, written to --status every 30 seconds ----
const started = Math.floor(Date.now() / 1000);
let connected = false, since = 0, me = opt.nick;
const chans = new Map();                                     // channel -> { nicks: Set, ops: Set }, lower case
const low = (s) => s.toLowerCase();
const chan = (name) => chans.get(low(name));

function writeStatus() {
  if (!opt.status) return;
  const bot = { name: me, connected, server: opt.server, started, version: '1.0',
    channels: [...chans.values()].map((c) => ({ name: c.name, users: c.nicks.size, op: c.ops.has(low(me)) })) };
  if (connected) bot.since = since;
  const data = { format: 1, updated: Math.floor(Date.now() / 1000), bots: [bot] };
  const tmp = path.join(path.dirname(opt.status), '.status-' + process.pid + '.tmp');
  fs.writeFileSync(tmp, JSON.stringify(data));
  fs.renameSync(tmp, opt.status);                            // the page never reads half a file
}
setInterval(writeStatus, (Number(opt.every) || 30) * 1000);

function gone(c, who) { c.nicks.delete(low(who)); c.ops.delete(low(who)); }

function track(cmd, nick, mid, text) {
  const args = mid.split(' ');
  const target = args[0];
  if (cmd === '001') {
    connected = true; since = Math.floor(Date.now() / 1000); me = target;
  } else if (cmd === '353' && chan(args[2])) {               // NAMES: <me> = #chan :@op +voice nick
    for (const n of text.split(' ').filter(Boolean)) {
      const name = low(n.replace(/^[@+%&~]+/, ''));
      chan(args[2]).nicks.add(name);
      if (n.startsWith('@')) chan(args[2]).ops.add(name);
    }
  } else if (cmd === 'JOIN') {
    const name = target || text;
    if (low(nick) === low(me)) chans.set(low(name), { name, nicks: new Set(), ops: new Set() });
    else if (chan(name)) chan(name).nicks.add(low(nick));
  } else if (cmd === 'PART' && chan(target)) {
    if (low(nick) === low(me)) chans.delete(low(target)); else gone(chan(target), nick);
  } else if (cmd === 'KICK' && chan(target)) {
    if (low(args[1]) === low(me)) chans.delete(low(target)); else gone(chan(target), args[1]);
  } else if (cmd === 'QUIT') {
    for (const c of chans.values()) gone(c, nick);
  } else if (cmd === 'NICK') {
    const to = low(text || target);
    for (const c of chans.values()) {
      if (c.nicks.delete(low(nick))) c.nicks.add(to);
      if (c.ops.delete(low(nick))) c.ops.add(to);
    }
    if (low(nick) === low(me)) me = text || target;
  } else if (cmd === 'MODE' && chan(target)) {               // +o/-o, one at a time or several: +oo a b
    const rest = args.slice(1).concat(text ? text.split(' ') : []);
    let sign = '+', i = 1;
    for (const m of rest[0] || '') {
      if (m === '+' || m === '-') { sign = m; continue; }
      if ('ovbkIeh'.includes(m) || (m === 'l' && sign === '+')) {   // the modes that take a word
        const who = rest[i++];
        if (m === 'o' && who) sign === '+' ? chan(target).ops.add(low(who)) : chan(target).ops.delete(low(who));
      }
    }
  } else if (cmd !== '366') return;
  if (cmd === '366' || cmd === '001' || (cmd === 'JOIN' && low(nick) === low(me))) writeStatus();
}

function handle(line) {
  // :nick!user@host COMMAND params :text
  const m = line.match(/^(?::(\S+) )?(\S+)(?: (.*?))?(?: :(.*))?$/);
  if (!m) return;
  const [, prefix = '', cmd, mid = '', text = ''] = m;
  const nick = prefix.split('!')[0];
  track(cmd, nick, mid, text);
  if (cmd === 'PING') return send(`PONG :${text || mid}`);
  if (cmd === '376' || cmd === '422') return send(`JOIN ${opt.channel}`);   // the end of the welcome
  if (cmd !== 'PRIVMSG') return;
  const target = mid.split(' ')[0];
  const where = target.startsWith('#') ? target : nick;          // a channel, or privately back
  const [word, ...rest] = text.trim().split(/\s+/);
  const command = (where === nick && !word.startsWith('!') ? '!' + word : word).toLowerCase();
  if (command === '!hello') say(where, `Hello, ${nick}!`);
  else if (command === '!echo') say(where, rest.join(' ') || 'Echo what?');
}

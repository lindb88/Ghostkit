// A small IRC bot in Node.js, with nothing but Node's own modules. Test it with irclab:
//   python3 -m irclab run examples/node/hellobot.scenario
// Run it for real:  node hellobot.js --server irc.libera.chat --port 6667 --channel '#mytest'
'use strict';
const net = require('net');

const opt = { server: '127.0.0.1', port: 6667, nick: 'HelloBot', channel: '#test' };
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
sock.on('close', () => process.exit(0));

function handle(line) {
  // :nick!user@host COMMAND params :text
  const m = line.match(/^(?::(\S+) )?(\S+)(?: (.*?))?(?: :(.*))?$/);
  if (!m) return;
  const [, prefix = '', cmd, mid = '', text = ''] = m;
  const nick = prefix.split('!')[0];
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

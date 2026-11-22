"""irclab: the server's behavior, and the scenario runner passing a good bot and failing a bad one."""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from irclab import IRCServer, User, q_expected, until   # noqa: E402
from irclab.scenario import Runner, StepFailed, parse   # noqa: E402


def run_scenario(text, folder=None):
    out = []
    ok = asyncio.run(Runner(text, out=out.append, base=folder or os.path.join(ROOT, 'examples')).run())
    return ok, out


class TestServer(unittest.TestCase):
    def test_channel_basics(self):
        async def go():
            s = await IRCServer('plain').start()
            a, b = User('Alice'), User('Bob')
            await a.connect(s.port)
            await b.connect(s.port)
            await a.join('#x')
            await b.join('#x')
            self.assertTrue(s.is_op('#x', 'alice'))                  # the first in gets ops
            self.assertFalse(s.is_op('#x', 'bob'))
            a.say('#x', 'hello')
            line = await b.expect(lambda l: l.command == 'PRIVMSG', 5)
            self.assertEqual((line.nick, line.target, line.text), ('Alice', '#x', 'hello'))
            a.send('MODE #x +o Bob')
            await until(lambda: s.is_op('#x', 'bob'), 5)
            a.close()
            b.close()
            await s.stop()
        asyncio.run(go())

    def test_libera_services(self):
        async def go():
            s = await IRCServer('libera').start()
            s.add_account('alice', 'pw')
            a = User('Alice')
            await a.connect(s.port)
            await a.identify('alice', 'pw')
            await until(lambda: s.client('Alice').account == 'alice', 5)
            a.close()
            await s.stop()
        asyncio.run(go())

    def test_quakenet_q_challenge(self):
        async def go():
            s = await IRCServer('quakenet').start()
            s.add_account('alice', 'pw')
            a = User('Alice')
            await a.connect(s.port)
            await a.q_auth('alice', 'pw')
            await until(lambda: s.client('Alice').account == 'alice', 5)
            a.close()
            await s.stop()
        asyncio.run(go())
        self.assertEqual(len(q_expected('a', 'b', 'c')), 64)


class TestScenario(unittest.TestCase):
    def test_parse(self):
        steps = parse('# comment\n\nAlice say #test "two words"  \nexpect Alice from Bot in #test x within 3\n')
        self.assertEqual(steps, [(3, ['Alice', 'say', '#test', 'two words']),
                                 (4, ['expect', 'Alice', 'from', 'Bot', 'in', '#test', 'x', 'within', '3'])])
        with self.assertRaises(StepFailed):
            parse('Alice say "unclosed')

    def test_echobot_passes(self):
        with open(os.path.join(ROOT, 'examples', 'echobot.scenario')) as f:
            ok, out = run_scenario(f.read())
        self.assertTrue(ok, '\n'.join(out))
        self.assertTrue(out[-1].endswith('passed'), out[-1])
        self.assertFalse([l for l in out if l.startswith('not ok')])

    def test_wrong_answer_fails(self):
        text = ('bot python3 echobot.py --port {port} --fast --channel #test\n'
                'wait EchoBot in #test\n'
                'user Alice\n'
                'Alice join #test\n'
                'Alice say #test !hello\n'
                'expect Alice from EchoBot in #test "Goodbye" within 2\n'
                'Alice say #test !hello\n')
        ok, out = run_scenario(text)
        self.assertFalse(ok)
        bad = [l for l in out if l.startswith('not ok')]
        self.assertEqual(len(bad), 1)
        self.assertIn('line 6', bad[0])
        self.assertIn('1 not run', out[-1])                           # it stops at the first failure

    def test_refute_catches_an_answer(self):
        text = ('bot python3 echobot.py --port {port} --fast --channel #test\n'
                'wait EchoBot in #test\n'
                'user Alice\n'
                'Alice join #test\n'
                'Alice say #test !hello\n'
                'refute Alice from EchoBot in #test Hello for 2\n')
        ok, out = run_scenario(text)
        self.assertFalse(ok, '\n'.join(out))

    def test_crashing_bot_shows_its_output(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'bad.py'), 'w') as f:
                f.write('print("starting up")\nraise SystemExit("config file missing")\n')
            ok, out = run_scenario('bot python3 bad.py\nwait BadBot online within 5\n', d)
        self.assertFalse(ok)
        text = '\n'.join(out)
        self.assertIn('exited', text)
        self.assertIn('config file missing', text)

    def test_on_server_and_env(self):
        seen = {}
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'cfgbot.py'), 'w') as f:
                f.write('import os, socket\n'
                        'port = int(open("port.txt").read())\n'
                        's = socket.create_connection(("127.0.0.1", port))\n'
                        'nick = os.environ["BOTNICK"]\n'
                        's.sendall(("NICK %s\\r\\nUSER x 0 * :x\\r\\n" % nick).encode())\n'
                        'import time; time.sleep(30)\n')

            def on_server(server):
                seen['port'] = server.port
                with open(os.path.join(d, 'port.txt'), 'w') as f:
                    f.write(str(server.port))
            out = []
            r = Runner('bot python3 cfgbot.py\nwait EnvBot online\n', out=out.append, base=d,
                       on_server=on_server, env={'BOTNICK': 'EnvBot'})
            ok = asyncio.run(r.run())
        self.assertTrue(ok, '\n'.join(out))
        self.assertIn('port', seen)

    def test_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, 'one.py')
            subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'bundle_irclab.py'), out, 'header line'],
                           check=True, capture_output=True)
            import importlib.util
            spec = importlib.util.spec_from_file_location('irclab_one', out)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with open(os.path.join(ROOT, 'examples', 'echobot.scenario')) as f:
                text = f.read()
            ok = asyncio.run(mod.Runner(text, out=lambda l: None, base=os.path.join(ROOT, 'examples')).run())
        self.assertTrue(ok)

    @unittest.skipUnless(shutil.which('node'), 'no node')
    def test_node_bot(self):
        with open(os.path.join(ROOT, 'examples', 'node', 'hellobot.scenario')) as f:
            ok, out = run_scenario(f.read(), os.path.join(ROOT, 'examples', 'node'))
        self.assertTrue(ok, '\n'.join(out))

    @unittest.skipUnless(shutil.which('go'), 'no go')
    def test_go_bot(self):
        with open(os.path.join(ROOT, 'examples', 'go', 'hellobot.scenario')) as f:
            ok, out = run_scenario(f.read(), os.path.join(ROOT, 'examples', 'go'))
        self.assertTrue(ok, '\n'.join(out))

    def test_command_line(self):
        r = subprocess.run([sys.executable, '-m', 'irclab', 'run', os.path.join('examples', 'echobot.scenario')],
                           cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()

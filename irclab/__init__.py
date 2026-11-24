"""irclab: test IRC bots on your own machine, in any language.

    from irclab import IRCServer, User, until

    server = await IRCServer('plain').start()
    alice = await User('Alice').connect(server.port)
    ...

or, for a bot in any language, a scenario file: python3 -m irclab run hello.scenario
See docs/irclab.md. Part of ghostkit (MIT license). Python 3.8+, the standard library only.
"""

from .client import Line, User, until
from .server import IRCServer, q_expected

__version__ = '0.1.4'
__all__ = ['IRCServer', 'User', 'Line', 'until', 'q_expected', '__version__']

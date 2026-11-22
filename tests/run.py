#!/usr/bin/env python3
"""python3 tests/run.py [-v]: every ghostkit test (unittest; what needs a missing tclsh is skipped)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
suite = unittest.defaultTestLoader.discover(HERE, pattern='test_*.py', top_level_dir=os.path.dirname(HERE))
result = unittest.TextTestRunner(verbosity=2 if '-v' in sys.argv else 1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)

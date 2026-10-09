# -*- coding: utf-8 -*-
"""单元测试入口：环境没有 pytest，tests/ 也没有 __init__.py，只能按文件路径加载。"""
import glob
import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))
os.chdir(ROOT)


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for path in sorted(glob.glob("tests/test_*.py")):
        name = os.path.splitext(os.path.basename(path))[0]
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        suite.addTests(loader.loadTestsFromModule(module))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

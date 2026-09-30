"""The pack runs alone and offline: sockets raise, and the sources reach for nothing outside.

The second half reads the sources (``ast``) and proves by construction what the first half
proves by running: no network module, no model client, no wall clock, no environment variable.
"""
import ast
import os
import re
import socket
import unittest
import urllib.request

from lab import fsx, offline
from tests import _util as U

CODE = ("lab", "corpus", "eval", "scenarios", "tools")
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "imaplib",
                   "poplib", "telnetlib", "xmlrpc", "socketserver", "asyncio", "websockets", "paramiko", "grpc"}
MODEL_MODULES = {"anthropic", "openai", "google", "mistralai", "cohere", "transformers", "torch", "litellm", "langchain"}
STDLIB_ALLOWED = {"__future__", "argparse", "ast", "bisect", "contextlib", "copy", "dataclasses", "datetime", "hashlib", "importlib", "io",
                  "json", "os", "random", "re", "shutil", "subprocess", "sys", "time", "types", "typing"}
OWN = {"lab", "corpus", "_common", "run_all", "score"}
THIRD_PARTY = {"yaml"}


def imports_of(rel: str) -> set:
    tree = ast.parse(fsx.read_bytes(os.path.join(U.ROOT, *rel.split("/"))).decode("utf-8"), filename=rel)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def source(rel: str) -> str:
    return fsx.read_bytes(os.path.join(U.ROOT, *rel.split("/"))).decode("utf-8")


class SocketsAreBlocked(unittest.TestCase):
    def test_the_guard_is_installed_before_any_test_runs(self):
        self.assertTrue(offline.is_enforced())

    def test_creating_a_socket_raises(self):
        with self.assertRaises(offline.OfflineViolation):
            socket.socket()
        with self.assertRaises(offline.OfflineViolation):
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def test_connecting_and_resolving_raise(self):
        with self.assertRaises(offline.OfflineViolation):
            socket.create_connection(("service.invalid", 443), timeout=1)
        with self.assertRaises(offline.OfflineViolation):
            socket.getaddrinfo("service.invalid", 443)

    def test_a_library_that_opens_a_connection_fails_loudly(self):
        with self.assertRaises(offline.OfflineViolation):
            urllib.request.urlopen("http://service.invalid/", timeout=1)

    def test_enforce_is_idempotent(self):
        offline.enforce()
        offline.enforce()
        self.assertTrue(offline.is_enforced())


class SourcesReachForNothing(unittest.TestCase):
    def test_there_are_sources_to_read(self):
        self.assertGreater(len(U.python_files(*CODE)), 40)

    def test_only_the_standard_library_pyyaml_and_the_pack_itself_are_imported(self):
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                extra = imports_of(rel) - STDLIB_ALLOWED - OWN - THIRD_PARTY - ({"socket"} if rel == "lab/offline.py" else set())
                self.assertEqual(extra, set())

    def test_no_network_module_outside_the_guard_itself(self):
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                allowed = {"socket"} if rel == "lab/offline.py" else set()
                self.assertEqual((imports_of(rel) & NETWORK_MODULES) - allowed, set())

    def test_no_model_client_and_no_model_hook(self):
        words = re.compile(r"(?<![a-z])(?:anthropic|openai|api_?key|claude|llm|prompt|completion|chat_?model)(?![a-z])")
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                self.assertEqual(imports_of(rel) & MODEL_MODULES, set())
                text = source(rel).lower()
                if rel == "tools/scan.py":                  # the scanner names the patterns it looks for
                    text = text.replace("anthropic.com", "").replace("api[_-]?key", "")
                self.assertEqual(words.findall(text), [])

    def test_yaml_is_imported_only_by_the_reader_and_by_one_reference(self):
        users = [rel for rel in U.python_files(*CODE) if "yaml" in imports_of(rel)]
        self.assertEqual(users, ["corpus/reference_routes.py", "lab/yamlio.py"])

    def test_subprocess_is_used_only_to_ask_git_for_the_commit(self):
        users = [rel for rel in U.python_files(*CODE) if "subprocess" in imports_of(rel)]
        self.assertEqual(users, ["eval/score.py", "tools/manifest.py"])
        for rel in users:
            self.assertIn('"git"', source(rel))
            self.assertNotIn("shell=True", source(rel))

    def test_the_wall_clock_is_never_read(self):
        forbidden = ("datetime.now", "utcnow", ".today(", "time.time", "time.localtime", "time.gmtime", "time.strftime")
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                self.assertEqual([w for w in forbidden if w in source(rel)], [])
        timers = [rel for rel in U.python_files(*CODE) if "time" in imports_of(rel)]
        self.assertEqual(timers, ["scenarios/run_all.py"])          # a stopwatch for the console line, never written to a file

    def test_no_environment_variable_is_read(self):
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                self.assertEqual([w for w in ("os.environ", "getenv", "os.getlogin", "getpass", "gethostname", "platform.node")
                                  if w in source(rel)], [])

    def test_the_lab_does_not_import_the_generator_and_the_generator_does_not_import_the_lab(self):
        for rel in U.python_files("lab"):
            self.assertEqual(imports_of(rel) & {"corpus", "score", "_common", "run_all"}, set(), rel)
        for rel in U.python_files("corpus"):
            self.assertNotIn("lab", imports_of(rel), rel)

    def test_nothing_is_evaluated_from_text(self):
        for rel in U.python_files(*CODE):
            with self.subTest(file=rel):
                tree = ast.parse(source(rel))
                calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
                self.assertEqual(calls & {"eval", "exec", "compile", "__import__"}, set())
                self.assertNotIn("yaml.load(", source(rel).replace("yaml.load(text, Loader=_UniqueKeyLoader)", ""))
                self.assertNotIn("pickle", source(rel))


if __name__ == "__main__":
    unittest.main()

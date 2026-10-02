"""The YAML writer of the generator: a style changes how a file is written, never what it means.

Every style is checked here against plain PyYAML (``yaml.safe_load``), not against the lab. The
lab's own reader is checked only on the styles of the development and stress suites; the
reserved styles (``RESERVED``) are left for the blind run and are never fed to the lab by the
author - this file is where that promise is kept honest.
"""
import itertools
import random
import unittest

import yaml

from corpus import generate as G
from corpus import yamlout
from lab import yamlio
from tests import _util as U

RESERVED = ["single-quoted", "doc-markers", "bom", "anchors", "port-string", "tz-alias"]
USED_BY_THE_AUTHOR = sorted(s for s in G.SUITES["stress"]["styles"] if s in yamlout.WRITING_STYLES)


def documents() -> list:
    return [U.topology_doc(public=("shop", "orders"), admin=("console",)),
            U.route_doc("shop", 8080, prefix="/api", priority=20, enabled=False),
            U.route_doc("orders", 8081, host="Orders.Valdora.example", entrypoints=["public", "admin"]),
            U.policy_doc(max_lag=30),
            {"text": ["yes", "no", "null", "~", "on", "off", "true", "12:30", "02:30", "007", "1e3", "0x10", "", " padded ",
                      "a: b", "# not a comment", "it's", 'say "hi"', "città", "- dash", "{brace}", "[bracket]", "*star", "&amp", "!bang", "%pct", "@at", "`tick`"],
             "numbers": [0, 7, -3, 8080], "flags": [True, False], "nothing": None, "empty": {}, "none": [],
             "nested": {"a": {"b": {"c": ["x", {"d": "e"}]}}}, "repeated": ["same", "same", "same"]}]


def plain_load(data: bytes):
    return yaml.safe_load(data.decode("utf-8-sig"))


class Emitter(unittest.TestCase):
    def test_the_lists_of_styles_are_the_ones_documented(self):
        self.assertEqual(sorted(RESERVED), sorted(s for s in yamlout.ALL_STYLES + ["tz-alias"] if s not in G.SUITES["stress"]["styles"]))
        self.assertEqual(G.SUITES["dev"]["styles"], [])
        self.assertEqual(G.SUITES["holdout"]["styles"], [])
        self.assertEqual([s for s in RESERVED if s in G.SUITES["stress"]["styles"]], [])

    def test_default_style_round_trips(self):
        for doc in documents():
            self.assertEqual(plain_load(yamlout.emit(doc)), doc)

    def test_every_writing_style_alone_round_trips_with_plain_pyyaml(self):
        for style in yamlout.WRITING_STYLES:
            for n, doc in enumerate(documents()):
                with self.subTest(style=style, doc=n):
                    self.assertEqual(plain_load(yamlout.emit(doc, [style], random.Random(f"{style}:{n}"))), doc)

    def test_pairs_and_triples_of_writing_styles_round_trip_with_plain_pyyaml(self):
        combos = list(itertools.combinations(yamlout.WRITING_STYLES, 2)) + list(itertools.combinations(yamlout.WRITING_STYLES, 3))
        self.assertEqual(len(combos), 45 + 120)
        for combo in combos:
            for n, doc in enumerate(documents()):
                with self.subTest(styles=combo, doc=n):
                    self.assertEqual(plain_load(yamlout.emit(doc, combo, random.Random(f"{combo}:{n}"))), doc)

    def test_styles_change_the_bytes(self):
        doc = documents()[0]
        default = yamlout.emit(doc)
        for style in yamlout.WRITING_STYLES:
            with self.subTest(style=style):
                self.assertNotEqual(yamlout.emit(doc, [style], random.Random(style)), default)

    def test_the_writer_is_deterministic(self):
        doc = documents()[0]
        styles = ["comments", "key-order", "flow"]
        self.assertEqual(yamlout.emit(doc, styles, random.Random("s")), yamlout.emit(doc, styles, random.Random("s")))

    def test_default_output_is_lf_utf8_without_a_byte_order_mark(self):
        data = yamlout.emit(documents()[4])
        self.assertNotIn(b"\r", data)
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("città".encode("utf-8"), data)
        self.assertTrue(data.endswith(b"\n"))


class LabReaderOnTheStylesTheAuthorUses(unittest.TestCase):
    """Only the styles of the stress suite: the reserved ones are not run against the lab here."""

    def test_no_reserved_style_is_exercised_in_this_class(self):
        self.assertEqual([s for s in USED_BY_THE_AUTHOR if s in RESERVED], [])
        self.assertEqual(USED_BY_THE_AUTHOR, ["comments", "crlf", "double-quoted", "flow", "indent-4", "key-order"])

    def test_the_lab_reads_the_same_data_as_plain_pyyaml(self):
        for size in (0, 1, 2, 3):
            for combo in itertools.combinations(USED_BY_THE_AUTHOR, size):
                for n, doc in enumerate(documents()):
                    with self.subTest(styles=combo, doc=n):
                        data = yamlout.emit(doc, combo, random.Random(f"lab:{combo}:{n}"))
                        loaded, problem = yamlio.loads(data.decode("utf-8"))
                        self.assertIsNone(problem)
                        self.assertEqual(loaded, doc)

    def test_the_lab_refuses_a_repeated_key_that_plain_pyyaml_silently_accepts(self):
        text = "service: shop\nservice: console\n"
        self.assertEqual(yaml.safe_load(text), {"service": "console"})        # last one wins, silently
        loaded, problem = yamlio.loads(text)
        self.assertIsNone(loaded)
        self.assertIn("duplicate", problem)


if __name__ == "__main__":
    unittest.main()

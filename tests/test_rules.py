"""Ordered rule files: inline tests of every rule, first match wins, a class nobody knows is never OK."""
import os
import unittest

from lab import findings, rules_engine

RULE_FILES = ["alerts.json", "backup_policy.json", "health.json", "route_lint.json", "severity.json"]


class RuleFiles(unittest.TestCase):
    def test_the_five_rule_files_exist_and_nothing_else(self):
        self.assertEqual(sorted(n for n in os.listdir(rules_engine.RULES_DIR) if n.endswith(".json")), RULE_FILES)

    def test_inline_tests_of_every_rule_pass(self):
        for name in RULE_FILES:
            with self.subTest(file=name):
                self.assertEqual(rules_engine.load(name).run_inline_tests(), [])

    def test_every_rule_has_id_rationale_and_at_least_one_test(self):
        seen = set()
        count = 0
        for name in RULE_FILES:
            rules = rules_engine.load(name)
            self.assertTrue(rules.sections(), name)
            for section in rules.sections():
                for rule in rules.rules(section):
                    count += 1
                    with self.subTest(file=name, rule=rule.get("id")):
                        self.assertTrue(rule["id"] and rule["id"] not in seen)
                        seen.add(rule["id"])
                        self.assertIsInstance(rule["when"], dict)
                        self.assertIsInstance(rule["then"], dict)
                        self.assertGreater(len(rule["rationale"]), 20)
                        self.assertGreaterEqual(len(rule["tests"]), 1)
                        for test in rule["tests"]:
                            self.assertTrue(test["id"].startswith(rule["id"]))
        self.assertEqual(count, 38)

    def test_inline_tests_catch_a_rule_shadowed_by_an_earlier_one(self):
        data = {"version": "t", "rules": [
            {"id": "X-1", "when": {"n": {"ge": 1}}, "then": {"class": "a"}, "rationale": "-", "tests": []},
            {"id": "X-2", "when": {"n": {"ge": 3}}, "then": {"class": "b"}, "rationale": "-",
             "tests": [{"id": "X-2-a", "input": {"n": 5}, "expect": {"class": "b"}}]}]}
        failures = rules_engine.RuleFile("t.json", data).run_inline_tests()
        self.assertEqual(failures, ["t.json:X-2-a: decided by X-1, expected X-2"])

    def test_versions_are_reported(self):
        versions = rules_engine.versions()
        self.assertEqual(sorted(versions), [n[:-5] for n in RULE_FILES])
        self.assertEqual(versions["health"], "2026.09.30-2")


class Matching(unittest.TestCase):
    def test_first_match_wins(self):
        rules = rules_engine.RuleFile("t.json", {"version": "t", "rules": [
            {"id": "EXC", "when": {"n": 7}, "then": {"v": "exception"}},
            {"id": "GEN", "when": {"n": {"ge": 1}}, "then": {"v": "general"}},
            {"id": "ALL", "when": {}, "then": {"v": "default"}}]})
        self.assertEqual(rules.decide("rules", {"n": 7}), ({"v": "exception"}, "EXC"))
        self.assertEqual(rules.decide("rules", {"n": 8}), ({"v": "general"}, "GEN"))
        self.assertEqual(rules.decide("rules", {"n": 0}), ({"v": "default"}, "ALL"))

    def test_a_missing_fact_never_matches(self):
        self.assertFalse(rules_engine.matches({"n": {"ge": 0}}, {}))
        self.assertFalse(rules_engine.matches({"n": None}, {}))

    def test_true_is_not_one(self):
        self.assertFalse(rules_engine.matches({"flag": True}, {"flag": 1}))
        self.assertFalse(rules_engine.matches({"count": 0}, {"count": False}))
        self.assertTrue(rules_engine.matches({"flag": False}, {"flag": False}))

    def test_a_value_of_the_wrong_type_does_not_match_and_does_not_raise(self):
        self.assertFalse(rules_engine.matches({"n": {"ge": 3}}, {"n": "many"}))
        self.assertFalse(rules_engine.matches({"n": {"ge": 3}}, {"n": None}))

    def test_an_unknown_operator_is_an_error_not_a_silent_pass(self):
        with self.assertRaises(ValueError):
            rules_engine.matches({"n": {"roughly": 3}}, {"n": 3})

    def test_operators(self):
        facts = {"n": 3, "c": "x"}
        for when, want in [({"n": {"eq": 3}}, True), ({"n": {"ne": 3}}, False), ({"n": {"gt": 2}}, True),
                           ({"n": {"lt": 3}}, False), ({"n": {"le": 3}}, True), ({"c": {"in": ["x", "y"]}}, True),
                           ({"c": {"not_in": ["x"]}}, False), ({"n": {"ge": 2, "lt": 3}}, False)]:
            with self.subTest(when=when):
                self.assertEqual(rules_engine.matches(when, facts), want)


class Severity(unittest.TestCase):
    def test_a_class_no_rule_knows_is_failed_never_ok(self):
        self.assertEqual(findings.verdict_of_class("something_nobody_wrote_a_rule_for"), ("FAILED", "S-999"))

    def test_verdicts_of_the_twelve_planted_classes(self):
        want = {"route_missing_service": "BLOCKED", "route_duplicate_shadow": "BLOCKED", "admin_on_public": "BLOCKED",
                "retention_deletes_only_valid": "BLOCKED", "health_probes_process": "FAILED", "restart_loop": "FAILED",
                "backup_truncated": "FAILED", "block_corrupt": "FAILED", "job_order_inverted": "FAILED",
                "repo_b_stale": "ALERT", "window_missed": "ALERT", "tz_mixed": "ALERT"}
        self.assertEqual({cls: findings.verdict_of_class(cls)[0] for cls in want}, want)

    def test_precedence_and_exit_codes(self):
        def f(cls):
            return findings.make(cls, "T", "file")
        self.assertEqual(findings.overall([]), "OK")
        self.assertEqual(findings.overall([f("tz_mixed")]), "ALERT")
        self.assertEqual(findings.overall([f("tz_mixed"), f("admin_on_public")]), "BLOCKED")
        self.assertEqual(findings.overall([f("tz_mixed"), f("admin_on_public"), f("block_corrupt")]), "FAILED")
        self.assertEqual([findings.exit_code(v) for v in ("OK", "ALERT", "BLOCKED", "FAILED")], [0, 1, 2, 3])

    def test_every_restore_failure_class_is_failed(self):
        for cls in ("restore_mismatch", "repo_unreadable", "no_snapshot", "source_unreadable", "registry_tampered",
                    "backup_truncated", "block_corrupt"):
            self.assertEqual(findings.verdict_of_class(cls)[0], "FAILED", cls)


if __name__ == "__main__":
    unittest.main()

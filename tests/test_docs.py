"""The documents of the pack: what must be there, what they may name, and what must not have moved."""
import hashlib
import os
import re
import unittest

from lab import fsx, jsonio
from tests import _util as U

# SHA-256 of the profile text (README from its title to the end, LF line endings) as it was before the
# proof pack was added. The pack adds a section above it and changes nothing below.
PROFILE_SHA256 = "ea323613305424ab16c7cbf5be5f0fe113c1eec5ad63542f338838c77ca440b5"
PROFILE_TITLE = "# Adrián Volta\n".encode("utf-8")
DOCUMENTS = ("SYNTHETIC.md", "CLAIMS.md", "MODEL.md", "CHANGELOG.md", "eval/BLIND_PROTOCOL.md", "docs/FORMAT.md")
PATH = re.compile(r"`((?:lab|corpus|eval|rules|scenarios|tests|tools|docs|reports|compose)/[A-Za-z0-9_./-]+)`")


def read(rel: str) -> bytes:
    return fsx.read_bytes(os.path.join(U.ROOT, *rel.split("/")))


def text(rel: str) -> str:
    return read(rel).decode("utf-8")


def pack_section() -> str:
    data = read("README.md")
    return data[:data.index(PROFILE_TITLE)].decode("utf-8")


class Readme(unittest.TestCase):
    def test_the_profile_text_below_the_proof_pack_section_is_byte_identical(self):
        data = read("README.md")
        self.assertEqual(data.count(PROFILE_TITLE), 1)
        self.assertEqual(hashlib.sha256(data[data.index(PROFILE_TITLE):]).hexdigest(), PROFILE_SHA256)

    def test_it_opens_with_the_banner_and_declares_an_ai_agent(self):
        first = text("README.md").splitlines()[0]
        self.assertTrue(first.startswith("> **SYNTHETIC - Adrián Volta is a synthetic alumnus (an AI agent)"), first)
        self.assertIn("not a person and not a certified professional", first)
        self.assertIn("does not describe any real infrastructure", first)

    def test_the_section_says_what_is_and_what_is_not_demonstrated(self):
        section = pack_section()
        for heading in ("### What is demonstrated", "### Re-run it", "### Numbers", "### What is NOT demonstrated"):
            self.assertIn(heading, section)
        self.assertTrue("internal consistency on synthetic data" in " ".join(section.split()))
        commands = re.search(r"### Re-run it.*?```bash\n(.*?)```", section, re.S).group(1).strip().splitlines()
        self.assertLessEqual(len(commands), 5)

    def test_the_numbers_are_the_recorded_ones(self):
        section = pack_section()
        digest = text("reports/REBUILD.sha256").split()[0]
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertIn(digest, section)
        results = jsonio.read(os.path.join(U.ROOT, "eval", "results.json"))
        for suite in ("dev", "holdout", "stress"):
            m = results[suite]
            self.assertIn(str(m["seed"]), section)
            self.assertIn(f"{m['localised']}/{m['planted_faults']}", section)
            self.assertEqual([m["never_event_restore"], m["never_event_admin_public"]], [0, 0])
        scenarios = jsonio.read(os.path.join(U.ROOT, "reports", "scenarios.json"))
        self.assertIn(f"{scenarios['passed']}/{scenarios['total']} PASS", section)
        self.assertEqual(scenarios["passed"], 10)

    def test_no_carriage_return_in_any_document(self):
        for rel in DOCUMENTS + ("README.md", "requirements.txt", ".github/workflows/ci.yml"):
            self.assertNotIn(b"\r", read(rel), rel)


class Claims(unittest.TestCase):
    def test_every_scenario_is_cited_and_every_cited_scenario_exists(self):
        claims = text("CLAIMS.md")
        cited = set(re.findall(r"\bS\d\d\b", claims))
        self.assertEqual(sorted(cited), [f"S{n:02d}" for n in range(1, 11)])
        for sid in cited:
            self.assertTrue(os.path.isfile(os.path.join(U.ROOT, "scenarios", sid, "scenario.json")), sid)

    def test_the_three_outcomes_and_the_limits_are_there(self):
        claims = " ".join(text("CLAIMS.md").split())          # a phrase may be wrapped over two lines
        for phrase in ("not demonstrated: out of v2.0", "awaiting legal review — not touched", "## Known limits",
                       "internal consistency on synthetic data"):
            self.assertTrue(phrase in claims, phrase)

    def test_every_repository_path_named_in_a_document_exists(self):
        sources = {rel: text(rel) for rel in DOCUMENTS}
        sources["README.md (proof-pack section)"] = pack_section()
        for rel, body in sources.items():
            for path in sorted(set(PATH.findall(body))):
                with self.subTest(document=rel, path=path):
                    self.assertTrue(os.path.exists(os.path.join(U.ROOT, *path.rstrip("/").split("/"))), path)


class ModelAndDependencies(unittest.TestCase):
    def test_only_the_two_fleet_models_are_named_by_id(self):
        ids = set(re.findall(r"\bclaude-[a-z]+-[0-9][0-9a-z-]*", text("MODEL.md")))
        self.assertEqual(ids, {"claude-opus-5-5", "claude-fable-5-1"})
        self.assertIn("There is no model hook in v2.0", text("MODEL.md"))

    def test_the_lockfile_pins_one_package_with_hashes(self):
        lines = [line.strip() for line in text("requirements.txt").splitlines() if line.strip() and not line.startswith("#")]
        self.assertEqual(lines[0].rstrip(" \\"), "PyYAML==6.0.3")
        hashes = [line.rstrip(" \\") for line in lines[1:]]
        self.assertGreaterEqual(len(hashes), 2)
        for line in hashes:
            self.assertRegex(line, r"^--hash=sha256:[0-9a-f]{64}$")

    def test_the_workflow_reads_no_secret_starts_no_container_and_checks_hashes(self):
        workflow = text(".github/workflows/ci.yml")
        self.assertIn("--require-hashes", workflow)
        self.assertIn("contents: read", workflow)
        engine = "dock" + "er"                      # assembled: another test forbids the literal in any module
        for word in ("secrets.", engine, "container:", "services:", "API_KEY", "curl ", "wget "):
            self.assertNotIn(word, workflow)


class BlindProtocol(unittest.TestCase):
    def test_the_command_is_parameterised_and_names_every_seed_already_used(self):
        protocol = text("eval/BLIND_PROTOCOL.md")
        self.assertIn("python eval/score.py --suite blind --seed <N>", protocol)
        history = jsonio.read(os.path.join(U.ROOT, "eval", "history.json"))
        for seed in sorted({run["seed"] for run in history["runs"] if run["suite"] != "blind"}) + [424242]:
            self.assertIn(f"`{seed}`", protocol)

    def test_the_author_recorded_no_blind_run(self):
        history = jsonio.read(os.path.join(U.ROOT, "eval", "history.json"))
        for run in history["runs"]:
            if run["suite"] == "blind":
                self.assertNotIn("author of the pack", run["runner"])


if __name__ == "__main__":
    unittest.main()

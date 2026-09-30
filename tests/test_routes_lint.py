"""Route files, table resolution and the lint (facts computed by the code, decisions taken by rules/route_lint.json)."""
import os
import unittest

from lab import lint, routes, topology, yamlio
from tests import _util as U


def run(root):
    topo, problems = topology.load(root)
    assert topo is not None, problems
    return lint.run(root, topo), topo


def classes(result):
    return [[f["class"], f["file"], f["item"], f["rule"]] for f in result["findings"]]


class YamlReading(unittest.TestCase):
    def test_duplicate_key_is_rejected_not_silently_overwritten(self):
        doc, err = yamlio.loads("service: shop\nservice: console\n")
        self.assertIsNone(doc)
        self.assertIn("duplicate key", err)

    def test_two_documents_in_one_file_are_rejected(self):
        doc, err = yamlio.loads("a: 1\n---\nb: 2\n")
        self.assertIsNone(doc)
        self.assertTrue(err)

    def test_broken_yaml_gives_a_reason(self):
        doc, err = yamlio.loads("a: [1, 2\n")
        self.assertIsNone(doc)
        self.assertTrue(err)

    def test_an_unquoted_time_can_be_read_as_a_number_and_is_kept_as_such(self):
        doc, _ = yamlio.loads("at: 12:30\nearly: 02:30\nquoted: \"12:30\"\n")
        self.assertEqual(doc["at"], 750)              # YAML 1.1 sexagesimal integer: the schedule rule reports it
        self.assertEqual(doc["early"], "02:30")
        self.assertEqual(doc["quoted"], "12:30")


class ParseFile(unittest.TestCase):
    def parse(self, doc):
        return routes.parse_file("routes/x.yaml", doc, 0)

    def test_valid_file(self):
        parsed, reason = self.parse(U.route_doc("shop", 8080))
        self.assertIsNone(reason)
        self.assertEqual([(r.name, r.entrypoints, r.host, r.path_prefix, r.target_service, r.target_port, r.priority, r.enabled)
                          for r in parsed], [("shop-main", ("public",), "shop.valdora.example", "/", "shop", 8080, 0, True)])

    def test_all_or_nothing_on_a_malformed_file(self):
        cases = {
            "top level": ["not", "a", "mapping"],
            "unknown top key": dict(U.route_doc("shop", 8080), middlewares={}),
            "unknown router key": U.route_doc("shop", 8080, tls=True),
            "both entrypoint forms": U.route_doc("shop", 8080, entrypoints=["public"]),
            "empty routers": {"service": "shop", "routers": [], "backends": {}},
            "host not a string": U.route_doc("shop", 8080, host=12),
            "prefix without slash": U.route_doc("shop", 8080, prefix="api"),
            "priority not an integer": U.route_doc("shop", 8080, priority="high"),
            "enabled not a boolean": U.route_doc("shop", 8080, enabled="yes"),
            "target without port": {"service": "s", "routers": U.route_doc("s", 1)["routers"], "backends": {"s-backend": {"target": "s"}}},
        }
        for label, doc in cases.items():
            with self.subTest(case=label):
                parsed, reason = self.parse(doc)
                self.assertEqual(parsed, [])
                self.assertTrue(reason)

    def test_repeated_router_name_in_one_file(self):
        doc = U.route_doc("shop", 8080)
        doc["routers"].append(dict(doc["routers"][0]))
        self.assertEqual(self.parse(doc)[0], [])

    def test_extension_keys_are_tolerated(self):
        doc = U.route_doc("shop", 8080)
        doc["x-owner"] = "team"
        self.assertIsNone(self.parse(doc)[1])

    def test_host_and_prefix_are_normalised(self):
        parsed, _ = self.parse(U.route_doc("shop", 8080, host="Shop.Valdora.Example.", prefix="/api/"))
        self.assertEqual((parsed[0].host, parsed[0].path_prefix), ("shop.valdora.example", "/api"))

    def test_port_written_as_a_string_of_digits(self):
        doc = U.route_doc("shop", 8080)
        doc["routers"][0]["priority"] = "5"
        self.assertEqual(self.parse(doc)[0][0].priority, 5)


class Resolution(unittest.TestCase):
    def entries(self, *specs):
        routers = []
        for i, (name, prefix, priority) in enumerate(specs):
            routers.append(routes.Router("routes/f.yaml", name, ("public",), "h.valdora.example", prefix, "b", name, 80,
                                         priority, True, i))
        return routes.table(routers)

    def test_longest_prefix_wins(self):
        table = self.entries(("root", "/", 0), ("api", "/api", 0))
        self.assertEqual(routes.resolve(table, "public", "h.valdora.example", "/api/v1")["router"], "api")
        self.assertEqual(routes.resolve(table, "public", "h.valdora.example", "/apiary")["router"], "root")

    def test_priority_beats_prefix_length(self):
        table = self.entries(("root", "/", 10), ("api", "/api", 0))
        self.assertEqual(routes.resolve(table, "public", "h.valdora.example", "/api")["router"], "root")

    def test_earliest_wins_on_a_full_tie(self):
        table = self.entries(("first", "/", 0), ("second", "/", 0))
        self.assertEqual(routes.resolve(table, "public", "h.valdora.example", "/")["router"], "first")

    def test_host_is_case_insensitive_and_other_entrypoint_does_not_match(self):
        table = self.entries(("root", "/", 0))
        self.assertIsNotNone(routes.resolve(table, "public", "H.Valdora.Example", "/x"))
        self.assertIsNone(routes.resolve(table, "admin", "h.valdora.example", "/x"))
        self.assertIsNone(routes.resolve(table, "public", "other.valdora.example", "/x"))

    def test_disabled_router_is_not_in_the_table(self):
        off = routes.Router("routes/f.yaml", "off", ("public",), "h.valdora.example", "/", "b", "s", 80, 0, False, 0)
        self.assertEqual(routes.table([off]), [])

    def test_diff_and_hash(self):
        before = self.entries(("root", "/", 0))
        after = self.entries(("root", "/", 0), ("api", "/api", 0))
        diff = routes.diff(before, after)
        self.assertEqual([len(diff["added"]), len(diff["removed"]), len(diff["changed"])], [1, 0, 0])
        self.assertEqual(routes.diff(after, before)["removed"][0]["router"], "api")
        changed = self.entries(("root", "/", 5))
        self.assertEqual(len(routes.diff(before, changed)["changed"]), 1)
        self.assertNotEqual(routes.table_sha256(before), routes.table_sha256(after))
        self.assertEqual(routes.table_sha256(before), routes.table_sha256(self.entries(("root", "/", 0))))


class Lint(U.TempCase):
    def instance(self, routes_, **topo):
        return U.routing_instance(self.tmp, U.topology_doc(**topo), routes_)

    def test_clean_instance_is_accepted(self):
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080),
                              "routes/internal/console.yaml": U.route_doc("console", 9000, entrypoint="admin")})
        result, topo = run(root)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["accepted"]), 2)
        self.assertEqual(lint.reach_matrix(result["accepted"], topo)["admin_public_edges"], [])

    def test_route_to_a_service_that_does_not_exist(self):
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080), "routes/ghost.yaml": U.route_doc("ghost", 8080)})
        result, _ = run(root)
        self.assertEqual(classes(result), [["route_missing_service", "routes/ghost.yaml", "ghost-main", "RL-040"]])
        self.assertIsNone(result["accepted"])

    def test_backend_name_not_defined_in_the_file(self):
        doc = U.route_doc("shop", 8080)
        doc["routers"][0]["backend"] = "somewhere-else"
        result, _ = run(self.instance({"routes/shop.yaml": doc}))
        self.assertEqual(classes(result), [["route_missing_service", "routes/shop.yaml", "shop-main", "RL-030"]])

    def test_wrong_port(self):
        result, _ = run(self.instance({"routes/shop.yaml": U.route_doc("shop", 8081)}))
        self.assertEqual(classes(result), [["route_wrong_port", "routes/shop.yaml", "shop-main", "RL-060"]])

    def test_unknown_entrypoint_is_not_guessed(self):
        result, _ = run(self.instance({"routes/shop.yaml": U.route_doc("shop", 8080, entrypoint="web")}))
        self.assertEqual(classes(result), [["route_unreadable", "routes/shop.yaml", "shop-main", "RL-020"]])

    def test_unreadable_file_blocks_the_table(self):
        result, _ = run(self.instance({"routes/shop.yaml": U.route_doc("shop", 8080), "routes/broken.yaml": "routers: [\n"}))
        self.assertEqual([c[:2] for c in classes(result)], [["route_unreadable", "routes/broken.yaml"]])
        self.assertIsNone(result["accepted"])

    def test_duplicate_in_a_forgotten_sub_folder_names_both_files_once(self):
        copy = U.route_doc("shop", 8080, name="shop-old", priority=9)
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080), "routes/old/2025/shop.yaml": copy})
        result, _ = run(root)
        self.assertEqual(len(result["findings"]), 1)
        f = result["findings"][0]
        self.assertEqual([f["class"], f["file"], f["also"], f["item"], f["rule"]],
                         ["route_duplicate_shadow", "routes/old/2025/shop.yaml", ["routes/shop.yaml"], None, "RL-070"])
        self.assertEqual(f["detail"]["winner"], "shop-old")

    def test_duplicate_with_different_case_and_trailing_slash_is_still_a_duplicate(self):
        copy = U.route_doc("shop", 8080, name="shop-b", host="SHOP.valdora.example", prefix="/api/")
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080, prefix="/api"), "routes/shop-b.yml": copy})
        self.assertEqual([c[0] for c in classes(run(root)[0])], ["route_duplicate_shadow"])

    def test_disabled_copy_is_not_a_duplicate(self):
        copy = U.route_doc("shop", 8080, name="shop-old", enabled=False)
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080), "routes/old/shop.yaml": copy})
        self.assertEqual(run(root)[0]["findings"], [])

    def test_same_host_on_the_two_planes_is_not_a_duplicate(self):
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080),
                              "routes/console.yaml": U.route_doc("console", 9000, entrypoint="admin", host="shop.valdora.example")})
        self.assertEqual(run(root)[0]["findings"], [])

    def test_files_that_are_not_route_files_are_not_loaded(self):
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080), "routes/NOTES.txt": "routers: [\n",
                              "routes/shop.yaml.disabled": "routers: [\n", "policy/other.yaml": "x: [\n"})
        result, _ = run(root)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["files"], ["routes/shop.yaml"])

    def test_admin_service_on_a_public_entrypoint(self):
        root = self.instance({"routes/console.yaml": U.route_doc("console", 9000, entrypoint="public")})
        result, topo = run(root)
        self.assertEqual(classes(result), [["admin_on_public", "routes/console.yaml", "console-main", "RL-050"]])
        self.assertIsNone(result["accepted"])
        self.assertEqual(lint.reach_matrix(result["accepted"], topo)["admin_public_edges"], [])
        self.assertEqual(lint.reach_matrix(result["provisional"], topo)["admin_public_edges"], [["public", "console"]])

    def test_admin_service_on_both_entrypoints(self):
        doc = U.route_doc("console", 9000)
        router = doc["routers"][0]
        del router["entrypoint"]
        router["entrypoints"] = ["admin", "public"]
        result, _ = run(self.instance({"routes/console.yaml": doc}))
        self.assertEqual([c[0] for c in classes(result)], ["admin_on_public"])

    def test_admin_service_attached_to_the_public_network(self):
        root = self.instance({"routes/console.yaml": U.route_doc("console", 9000, entrypoint="admin")},
                             admin_networks=["edge-public", "app-1"])
        result, _ = run(root)
        self.assertEqual(classes(result), [["admin_on_public", "topology.yaml", "console", "RL-S10"]])
        self.assertIsNone(result["accepted"])

    def test_a_disabled_admin_route_on_public_is_skipped_by_the_exception_on_top(self):
        root = self.instance({"routes/console.yaml": U.route_doc("console", 9000, entrypoint="public", enabled=False)})
        self.assertEqual(run(root)[0]["findings"], [])

    def test_public_service_on_the_admin_entrypoint_is_allowed(self):
        root = self.instance({"routes/shop.yaml": U.route_doc("shop", 8080, entrypoint="admin")})
        self.assertEqual(run(root)[0]["findings"], [])


class TopologyFile(U.TempCase):
    def test_malformed_topology_is_reported_not_guessed(self):
        doc = U.topology_doc()
        doc["services"]["shop"]["port"] = "eighty"
        doc["services"]["console"]["health"] = {"kind": "http"}
        doc["entrypoints"]["public"]["network"] = "nowhere"
        U.write_yaml(os.path.join(self.tmp, "topology.yaml"), doc)
        topo, problems = topology.load(self.tmp)
        self.assertIsNone(topo)
        self.assertEqual(len(problems), 3)

    def test_missing_topology(self):
        topo, problems = topology.load(self.tmp)
        self.assertIsNone(topo)
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()

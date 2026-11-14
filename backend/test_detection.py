"""Tests for the default migration quality gates, using the real AROYA defects."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import detection as d


class SourceContract(unittest.TestCase):
    def test_dropped_auth_header_is_a_blocker(self):
        # Mule forwarded the Seaware session header; the generated service forwarded only Authorization.
        findings = d.source_contract_diff(
            source_forwarded_headers=["Authorization", "X-Seaware-Authorization", "client_channel"],
            target_forwarded_headers=["Authorization"],
        )
        klasses = {f.klass for f in findings}
        self.assertIn("auth-passthrough", klasses)          # the booking blocker
        blockers = [f for f in findings if f.severity == "blocker"]
        self.assertTrue(any("X-Seaware-Authorization" in b.summary for b in blockers))

    def test_nothing_dropped_is_clean(self):
        self.assertEqual(d.source_contract_diff(["Authorization"], ["authorization"]), [])


class Parity(unittest.TestCase):
    def test_works_on_source_fails_on_target(self):
        f = d.parity("reservationCreate", 200, {"data": {"x": 1}}, 200,
                     {"data": None, "errors": [{"message": "not permitted"}]})
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].klass, "source-ok-target-fail")
        self.assertEqual(f[0].severity, "blocker")

    def test_http_error_on_target_only(self):
        f = d.parity("voyages", 200, {"data": {}}, 401, {"error": "unauthorized"})
        self.assertEqual(f[0].klass, "source-ok-target-fail")

    def test_both_ok_same_body_is_clean(self):
        self.assertEqual(d.parity("cities", 200, {"a": 1}, 200, {"a": 1}), [])


class ErrorSignatures(unittest.TestCase):
    def test_auth_security_context(self):
        f = d.classify_error('{"errors":[{"message":"Permission Error: Query is not permitted in the current security context"}]}')
        self.assertIsNotNone(f)
        self.assertEqual(f.klass, "auth-passthrough")

    def test_cms_ns_binary_wrapping(self):
        f = d.classify_error('<ns:binary xmlns:ns="http://ws.apache.org/commons/ns/payload">CjwhLS0=')
        self.assertEqual(f.klass, "content-wrapping")

    def test_timeout_circuit(self):
        self.assertEqual(d.classify_error("upstream 504 gateway timeout").klass, "timeout-circuit")

    def test_route_not_published(self):
        self.assertEqual(d.classify_error("no matching service found for path: /cms").klass, "route-not-published")

    def test_unknown_error_returns_none(self):
        self.assertIsNone(d.classify_error("some unrelated message"))


class DefaultGate(unittest.TestCase):
    def test_gate_blocks_and_emits_lessons(self):
        res = d.run_default_gate(
            source_headers=["Authorization", "X-Seaware-Authorization"],
            target_headers=["Authorization"],
            operations=[{"operation": "reservationCreate", "source_status": 200,
                         "source_body": {"data": {}}, "target_status": 200,
                         "target_body": {"errors": [{"message": "x"}]}}],
            error_texts=["not permitted in the current security context"],
        )
        self.assertFalse(res["passed"])
        self.assertTrue(res["blockers"])
        self.assertTrue(res["lessons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

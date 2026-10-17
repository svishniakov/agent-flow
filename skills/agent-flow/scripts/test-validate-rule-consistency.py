#!/usr/bin/env python3
"""Rule consistency validator fixtures."""

import tempfile
import unittest
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

spec = spec_from_file_location("validate_rule_consistency", Path(__file__).with_name("validate-rule-consistency.py"))
validator = module_from_spec(spec)
spec.loader.exec_module(validator)


class RuleConsistencyFixtures(unittest.TestCase):

    def test_repository_is_consistent(self):
        errors = []
        validator.check_single_source(errors)
        validator.check_stale_doc_patterns(errors)
        validator.check_documented_vocabulary(errors)
        self.assertEqual(errors, [])

    def test_duplicate_table_definition_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            scripts = Path(temporary)
            (scripts / "journal_structure.py").write_text("EVENT_STATUS_RULES = {}\n", encoding="utf-8")
            (scripts / "copy.py").write_text("EVENT_STATUS_RULES = {}\n", encoding="utf-8")
            errors = []
            validator.check_single_source(errors, scripts=scripts)
            self.assertTrue(any("EVENT_STATUS_RULES" in error and "copy.py" in error for error in errors), errors)

    def test_stale_doc_pattern_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            doc = Path(temporary) / "reference.md"
            doc.write_text("Run `python3 scripts/journal.py contract` first.\n", encoding="utf-8")
            errors = []
            validator.check_stale_doc_patterns(errors, docs=[doc])
            self.assertTrue(any("stale pattern" in error for error in errors), errors)

    def test_missing_documented_status_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            doc = Path(temporary) / "traceable-runs.md"
            doc.write_text("Only `planned` is documented.\n", encoding="utf-8")
            errors = []
            validator.check_documented_vocabulary(errors, doc=doc)
            self.assertTrue(any("`active`" in error for error in errors), errors)

    def test_retired_gate_name_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            doc = Path(temporary) / "reference.md"
            doc.write_text("Enforce Mitigation Gate before pass-with-risks.\n", encoding="utf-8")
            errors = []
            validator.check_retired_gate_names(errors, docs=[doc])
            self.assertTrue(any("Mitigation Gate" in error for error in errors), errors)

    def test_retired_gate_name_with_annotation_is_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            doc = Path(temporary) / "reference.md"
            doc.write_text("Risk Lifecycle Gate (входит в: Mitigation Gate, Resolution Gate).\n", encoding="utf-8")
            errors = []
            validator.check_retired_gate_names(errors, docs=[doc])
            self.assertEqual(errors, [])

    def test_retired_gate_name_spanning_line_wrap_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            doc = Path(temporary) / "reference.md"
            doc.write_text("The lane must satisfy Lane Boundary\nEvidence Gate before QA.\n", encoding="utf-8")
            errors = []
            validator.check_retired_gate_names(errors, docs=[doc])
            self.assertTrue(any("Lane Boundary Evidence Gate" in error and "wrap" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()

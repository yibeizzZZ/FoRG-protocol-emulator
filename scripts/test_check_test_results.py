"""Exercise the result validator's command-line exit status with XML fixtures."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


VALIDATOR = Path(__file__).with_name("check-test-results.py")


class CheckTestResultsTests(unittest.TestCase):
    def run_validator(self, xml):
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results.xml"
            if xml is not None:
                results.write_text(xml, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(VALIDATOR), str(results)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_accepts_passing_results(self):
        samples = [
            '<testsuites><testsuite><testcase name="passes"/></testsuite></testsuites>',
            '<testsuite tests="1" failures="0" errors="0">'
            '<testcase name="failure_in_name_is_not_a_failure"/></testsuite>',
            '<testsuites><testsuite><testcase name="passes"/></testsuite>'
            '<testsuite><testcase name="skips"><skipped/></testcase></testsuite></testsuites>',
        ]
        for xml in samples:
            with self.subTest(xml=xml):
                result = self.run_validator(xml)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_missing_malformed_or_unexecuted_results(self):
        samples = [
            None,
            "",
            "<testsuites>",
            "<testsuites/>",
            '<testsuite tests="1"/>',
            '<testsuite><testcase><skipped/></testcase></testsuite>',
            '<unrelated><testcase/></unrelated>',
        ]
        for xml in samples:
            with self.subTest(xml=xml):
                result = self.run_validator(xml)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("results.xml", result.stderr)

    def test_rejects_failures_and_errors(self):
        samples = [
            '<testsuite><testcase><failure message="assertion failed"/></testcase></testsuite>',
            '<testsuite><testcase><error message="simulation failed"/></testcase></testsuite>',
            '<testsuites><testsuite><testcase/></testsuite>'
            '<testsuite><testcase><failure/></testcase></testsuite></testsuites>',
            '<testsuite failures="1"><testcase/></testsuite>',
            '<testsuites errors="1"><testsuite><testcase/></testsuite></testsuites>',
            '<testsuite failures="invalid"><testcase/></testsuite>',
            '<testsuite errors="-1"><testcase/></testsuite>',
        ]
        for xml in samples:
            with self.subTest(xml=xml):
                result = self.run_validator(xml)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("results.xml", result.stderr)


if __name__ == "__main__":
    unittest.main()

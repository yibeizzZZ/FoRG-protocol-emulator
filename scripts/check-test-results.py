"""Fail CI unless a cocotb/JUnit XML report contains executed, passing tests."""

import argparse
import sys
import xml.etree.ElementTree as ET


def validate_results(path):
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("expected a testsuites or testsuite root element")

    # Check both detailed results and summary counts when a producer supplies them.
    for element in root.iter():
        if element.tag in {"failure", "error"}:
            raise ValueError(f"report contains a {element.tag}")
        if element.tag in {"testsuites", "testsuite"}:
            for attribute in ("failures", "errors"):
                if int(element.get(attribute, "0")) != 0:
                    raise ValueError(f"report has nonzero {attribute}")

    cases = list(root.iter("testcase"))
    executed = sum(case.find("skipped") is None for case in cases)
    if executed == 0:
        raise ValueError("report contains no executed tests")
    return executed, len(cases) - executed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", help="path to the XML test report")
    args = parser.parse_args()
    try:
        executed, skipped = validate_results(args.results)
    except (OSError, ET.ParseError, ValueError) as error:
        print(f"Invalid test results {args.results}: {error}", file=sys.stderr)
        return 1
    print(f"Verified {args.results}: {executed} passed, {skipped} skipped, no failures/errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())

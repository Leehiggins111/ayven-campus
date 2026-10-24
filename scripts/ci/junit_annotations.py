"""Print each failed pytest case from a JUnit XML file as a GitHub Actions error annotation.

Annotations can be read through the GitHub API, so a failure is visible even where the raw
job log cannot be downloaded.
"""

import sys
import xml.etree.ElementTree as ET


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def main(path: str) -> int:
    root = ET.parse(path).getroot()
    shown = 0
    for case in root.iter("testcase"):
        for problem in list(case.findall("failure")) + list(case.findall("error")):
            name = f"{case.get('classname', '')}::{case.get('name', '')}"
            detail = (problem.get("message") or "") + "\n" + (problem.text or "")
            print(f"::error title={_escape(name)[:200]}::{_escape(detail.strip()[-3500:])}")
            shown += 1
    print(f"{shown} failing test case(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

"""Check every config in datasets/ (what CI runs after a seed data change).

    python check_datasets.py        exit code 1 if any config has a problem
"""
import sys

import datasets


def main() -> int:
    found = datasets.list_datasets()
    if not found:
        print("No configs found in datasets/")
        return 1
    bad = 0
    for ds in found:
        problems = datasets.validate_dataset(ds)
        print(f"{'FAIL' if problems else 'ok  '} {ds.name}: {ds.n_records} records")
        for problem in problems:
            print(f"     - {problem}")
        bad += bool(problems)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

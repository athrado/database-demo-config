"""Load a config into a table, from the command line.

    python seed.py                          list the configs and the tables
    python seed.py retail                   load "retail" into the table from .env (EXT_DATA_TABLE)
    python seed.py retail --table demoretail
    python seed.py retail --reset           first empty the table (all of it, unless another config uses it too)
    python seed.py --all                    every config into its own default table (demoretail, ...)
    python seed.py --export backup.json     write the table's contents to a file

Which storage: Azure when EXT_DATA_ACCOUNT_URL is set (environment or .env), otherwise local files.
"""
import argparse
import json
import sys
from pathlib import Path

import datasets
from store import AzureTableStore, StoreError, get_store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("config", nargs="?", help="config name (a folder in datasets/)")
    parser.add_argument("--table", help="table to use instead of EXT_DATA_TABLE")
    parser.add_argument("--reset", action="store_true", help="empty the table first (all of it, unless another config uses it too)")
    parser.add_argument("--all", action="store_true", help="load every config into its default table")
    parser.add_argument("--export", metavar="FILE", help="write the table's contents to a JSON file")
    args = parser.parse_args()

    try:
        if args.all:
            for ds in datasets.list_datasets():
                if not ds.default_table:
                    print(f"- {ds.name}: no default_table, skipped")
                    continue
                store = get_store(ds.default_table)
                count = datasets.load_into(store, ds, reset=args.reset, wipe_all=not datasets.others_using_table(getattr(store, "table", None) or store.path.stem, ds.name))
                print(f"- {ds.name}: {count} records -> {store.describe()}")
            return 0

        store = get_store(args.table)
        if args.export:
            data = store.export()
            Path(args.export).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Wrote {sum(len(r) for r in data.values())} records from {store.describe()} to {args.export}")
            return 0

        if not args.config:
            print(f"Storage: {store.describe()}")
            try:
                print("Tables: " + (", ".join(store.list_tables()) or "(none yet)"))
            except StoreError as exc:
                print(f"Tables: could not be listed ({exc})")
            print("\nConfigs:")
            for ds in datasets.list_datasets():
                print(f"  {ds.name:<10} {ds.n_records:>3} records   table {ds.default_table or '-':<14} {ds.title}: {ds.description}")
            return 0

        ds = datasets.get_dataset(args.config)
        print(store.describe())
        if isinstance(store, AzureTableStore) and not args.table and ds.default_table and ds.default_table != store.table:
            print(f"Note: the usual table for '{ds.name}' is '{ds.default_table}', but this goes into '{store.table}'. "
                  f"Add --table {ds.default_table} to use the usual one.")
        count = datasets.load_into(store, ds, reset=args.reset, wipe_all=not datasets.others_using_table(getattr(store, "table", None) or store.path.stem, ds.name))
        print(f"Loaded {count} records of '{ds.name}'{' (reset first)' if args.reset else ''}.")
    except StoreError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Create a transactionally consistent SQLite backup of the configured database."""

import argparse
import sqlite3
from pathlib import Path

from .config import settings


def backup_database(destination: Path) -> Path:
    source = settings.db_path
    target = destination.expanduser().resolve()
    if target == source.resolve():
        raise ValueError("Backup destination must differ from the live database")
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as live, sqlite3.connect(target) as backup:
        live.backup(backup)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="path for the SQLite backup file")
    args = parser.parse_args()
    print(backup_database(args.destination))


if __name__ == "__main__":
    main()

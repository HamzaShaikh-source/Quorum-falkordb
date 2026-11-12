#!/usr/bin/env python
"""
Wipes Quorum's data from FalkorDB so the next start is a clean install: every graph named quorum:<tenant>.

    python scripts/reset_data.py                 # shows what would be deleted
    python scripts/reset_data.py --yes           # deletes it
    python scripts/reset_data.py --yes --tenant acme

Stop the backend first (or restart it afterwards): it keeps a copy of each tenant in memory and would write it back.
Docker alternative that also removes the database volume:  docker compose down -v
"""
import argparse
import os
import sys
from urllib.parse import urlparse

URL = os.getenv("QUORUM_FALKORDB_URL", "redis://localhost:6379")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="really delete")
    ap.add_argument("--tenant", help="only this tenant")
    a = ap.parse_args()
    from falkordb import FalkorDB

    u = urlparse(URL)
    db = FalkorDB(host=u.hostname or "localhost", port=u.port or 6379, username=u.username, password=u.password)
    graphs = [g for g in db.list_graphs() if g.startswith("quorum:") and (not a.tenant or g == f"quorum:{a.tenant}")]
    if not graphs:
        print("nothing to delete: already clean")
        return 0
    for g in graphs:
        print(("deleting " if a.yes else "would delete ") + g)
        if a.yes:
            db.select_graph(g).delete()
    if not a.yes:
        print("\nrun again with --yes to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

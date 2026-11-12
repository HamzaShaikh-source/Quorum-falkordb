"""
Regenerates backend/tests/fixtures/mcp_catalog.json from the live MCP catalog.
"""
import json
import sys
from pathlib import Path

# Add backend directory to sys.path
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "backend"))

from app.services.mcp_catalog import catalog

def main():
    target = root / "backend" / "tests" / "fixtures" / "mcp_catalog.json"
    live = [{k: v for k, v in x.items() if k != "doc"} for x in catalog()]
    target.write_text(json.dumps({"tools": live}, indent=2) + "\n", encoding="utf8")
    print(f"Updated {target} with {len(live)} tools.")

if __name__ == "__main__":
    main()

import os
import sys
import json

_current_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.abspath(os.path.join(_current_dir, ".."))
if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

from graph import queries

def test_enhanced():
    tenant = "stress_t03_2_multi_topics"
    g = queries.get_graph(tenant, "commons")

    # Enhanced query fulfilling CONTRACT.md:
    # { person, team, evidence_count: number, source_ids: string[] }
    # Plus owner_team of the sources
    cypher = """
    MATCH (t:Topic)
    WHERE t.id = $topic OR toLower(t.id) = toLower($topic) OR toLower(t.name) = toLower($topic)
    MATCH (p:Person)-[:AUTHORED|RESOLVED]->(s:Source)-[:ABOUT]->(t)
    OPTIONAL MATCH (p)-[:MEMBER_OF]->(p_team:Team)
    OPTIONAL MATCH (src_team:Team)-[:OWNS]->(s)
    RETURN p.id AS person_id,
           p.name AS person_name,
           p_team.name AS person_team,
           count(DISTINCT s) AS evidence,
           collect(DISTINCT s.id) AS source_ids,
           collect(DISTINCT src_team.name) AS source_owner_teams
    ORDER BY evidence DESC
    """
    res = g.query(cypher, {"topic": "Billing & Invoicing Architecture"})
    print("Enhanced query by topic title ('Billing & Invoicing Architecture'):")
    for r in res.result_set:
        print(f"  Person: {r[1]} ({r[0]}), Team: {r[2]}, Evidence: {r[3]}, Sources: {len(r[4])}, OwnerTeams: {r[5]}")

    res_id = g.query(cypher, {"topic": "billing"})
    print("\nEnhanced query by topic ID ('billing'):")
    for r in res_id.result_set:
        print(f"  Person: {r[1]} ({r[0]}), Team: {r[2]}, Evidence: {r[3]}, Sources: {len(r[4])}, OwnerTeams: {r[5]}")

if __name__ == "__main__":
    test_enhanced()

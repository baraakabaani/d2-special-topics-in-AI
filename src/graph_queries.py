"""
graph_queries.py — D2.
done by Khalid

Runs the 5 example Cypher queries against the Neo4j knowledge graph
and prints formatted results for the report.

Usage:
  python -m src.graph_queries
"""

import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(".env.local", override=True)

NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "changeme123")


def run_queries():
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

    queries = [
        (
            "1. Papers per year",
            """
            MATCH (p:Paper)
            RETURN p.year AS year, count(p) AS papers
            ORDER BY year DESC
            """,
        ),
        (
            "2. Most prolific authors (top 10)",
            """
            MATCH (p:Paper)-[:AUTHORED_BY]->(a:Author)
            RETURN a.name AS author, count(p) AS papers
            ORDER BY papers DESC LIMIT 10
            """,
        ),
        (
            "3. Most common topics (top 10)",
            """
            MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
            RETURN t.name AS topic, count(p) AS papers
            ORDER BY papers DESC LIMIT 10
            """,
        ),
        (
            "4. Papers sharing the topic 'agent' (top 5)",
            """
            MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic {name: 'agent'})
            RETURN p.doc_id AS doc_id, p.title AS title
            LIMIT 5
            """,
        ),
        (
            "5. Co-author pairs (top 10 by shared papers)",
            """
            MATCH (a1:Author)<-[:AUTHORED_BY]-(p:Paper)-[:AUTHORED_BY]->(a2:Author)
            WHERE a1.name < a2.name
            RETURN a1.name AS author1, a2.name AS author2, count(p) AS shared_papers
            ORDER BY shared_papers DESC LIMIT 10
            """,
        ),
    ]

    with driver.session() as session:
        for title, cypher in queries:
            print(f"\n{'='*60}")
            print(f"Query {title}")
            print('='*60)
            results = session.run(cypher)
            rows = results.data()
            if not rows:
                print("  (no results)")
            for row in rows:
                print("  " + "  |  ".join(f"{k}: {v}" for k, v in row.items()))

    driver.close()
    print("\nDone.")


if __name__ == "__main__":
    run_queries()

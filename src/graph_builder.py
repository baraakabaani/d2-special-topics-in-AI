"""
graph_builder.py — D2.
done by Khalid

Reads paper chunks from MongoDB (d2.chunks + d2.docs), extracts entities,
and loads them into a Neo4j knowledge graph.

Graph schema
------------
(:Paper)  – one node per academic paper
(:Author) – one node per unique author name
(:Topic)  – one node per normalized keyword extracted from title

Relationships
-------------
(:Paper)-[:AUTHORED_BY]->(:Author)
(:Paper)-[:HAS_TOPIC]->(:Topic)

Cypher examples (5 queries for the report):
  1. Papers per year
  2. Most prolific authors
  3. Most common topics
  4. Papers sharing a topic
  5. Co-author pairs

Usage:
  python -m src.graph_builder
"""

import logging
import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from neo4j import GraphDatabase
from pymongo import MongoClient

load_dotenv(".env.local", override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── connection details ────────────────────────────────────────────────────────
MONGO_URI  = os.getenv("MONGO_URI",  "mongodb://admin:changeme@localhost:27017/?authSource=admin")
NEO4J_URI  = os.getenv("NEO4J_URI",  "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS", "changeme123")

# Words to ignore when extracting topics from titles
STOPWORDS = {
    "with", "using", "from", "that", "this", "based", "large", "small",
    "model", "models", "learning", "neural", "network", "networks", "deep",
    "towards", "efficient", "approach", "method", "methods", "paper", "study",
    "analysis", "framework", "system", "systems", "data", "dataset", "bench",
    "evaluation", "survey", "review", "novel", "new", "improved", "simple",
    "better", "beyond", "without", "multi", "joint", "end", "scale", "high",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def extract_topics(title: str) -> list[str]:
    """Extract keywords from a paper title by filtering stopwords."""
    words = re.findall(r"[A-Za-z]{5,}", title)
    return list({w.lower() for w in words if w.lower() not in STOPWORDS})[:6]


def extract_authors(authors_str: str) -> list[str]:
    """Split comma- or semicolon-separated authors string into a clean list."""
    if not authors_str or authors_str == "Unknown":
        return []
    parts = re.split(r"[;,]", authors_str)
    return [a.strip() for a in parts if a.strip()]


# ── Cypher queries ────────────────────────────────────────────────────────────

CONSTRAINTS = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (p:Paper)  REQUIRE p.doc_id IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Author) REQUIRE a.name   IS UNIQUE",
    "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic)  REQUIRE t.name   IS UNIQUE",
]

MERGE_PAPER = """
MERGE (p:Paper {doc_id: $doc_id})
SET   p.title   = $title,
      p.year    = $year,
      p.venue   = $venue,
      p.authors = $authors
"""

MERGE_AUTHOR_REL = """
MATCH (p:Paper {doc_id: $doc_id})
MERGE (a:Author {name: $name})
MERGE (p)-[:AUTHORED_BY]->(a)
"""

MERGE_TOPIC_REL = """
MATCH (p:Paper {doc_id: $doc_id})
MERGE (t:Topic {name: $name})
MERGE (p)-[:HAS_TOPIC]->(t)
"""

# ── 5 example Cypher queries for the report ───────────────────────────────────

EXAMPLE_QUERIES = [
    (
        "1. Papers per year",
        "MATCH (p:Paper) RETURN p.year AS year, count(*) AS papers ORDER BY year DESC",
    ),
    (
        "2. Most prolific authors (top 5)",
        """
        MATCH (a:Author)<-[:AUTHORED_BY]-(p:Paper)
        RETURN a.name AS author, count(p) AS papers
        ORDER BY papers DESC LIMIT 5
        """,
    ),
    (
        "3. Most common topics (top 10)",
        """
        MATCH (t:Topic)<-[:HAS_TOPIC]-(p:Paper)
        RETURN t.name AS topic, count(p) AS papers
        ORDER BY papers DESC LIMIT 10
        """,
    ),
    (
        "4. Papers sharing a topic (e.g. 'language')",
        """
        MATCH (p:Paper)-[:HAS_TOPIC]->(t:Topic)
        WHERE t.name CONTAINS 'language'
        RETURN p.title AS title, t.name AS topic
        LIMIT 5
        """,
    ),
    (
        "5. Co-author pairs",
        """
        MATCH (a1:Author)<-[:AUTHORED_BY]-(p:Paper)-[:AUTHORED_BY]->(a2:Author)
        WHERE a1.name < a2.name
        RETURN a1.name AS author1, a2.name AS author2, p.title AS paper
        LIMIT 5
        """,
    ),
]


# ── GraphBuilder ──────────────────────────────────────────────────────────────

class GraphBuilder:

    def __init__(self):
        self.mongo  = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000).d2
        self.driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))
        log.info("Connected to MongoDB (d2) and Neo4j")

    def setup_constraints(self):
        with self.driver.session() as s:
            for q in CONSTRAINTS:
                s.run(q)
        log.info("Constraints verified")

    def load_all(self) -> dict:
        self.setup_constraints()

        # Read from docs collection (has enriched title/authors/abstract from enrich.py)
        docs = list(self.mongo.docs.find({}))
        log.info(f"Loading {len(docs)} papers into Neo4j...")

        paper_count = author_count = topic_count = 0

        for doc in docs:
            doc_id  = doc["doc_id"]
            title   = doc.get("title",   "Unknown")
            authors = doc.get("authors", "Unknown")
            year    = doc.get("year",    0)
            venue   = doc.get("venue",   "arXiv")

            # Paper node
            with self.driver.session() as s:
                s.run(MERGE_PAPER,
                      doc_id=doc_id, title=title,
                      year=year, venue=venue, authors=authors)
            paper_count += 1

            # Author nodes + edges
            for author in extract_authors(authors):
                with self.driver.session() as s:
                    s.run(MERGE_AUTHOR_REL, doc_id=doc_id, name=author)
                author_count += 1

            # Topic nodes + edges
            for topic in extract_topics(title):
                with self.driver.session() as s:
                    s.run(MERGE_TOPIC_REL, doc_id=doc_id, name=topic)
                topic_count += 1

        stats = {"papers": paper_count, "authors": author_count, "topics": topic_count}
        log.info(f"Done: {stats}")
        return stats

    def run_example_queries(self):
        print("\n── Example Cypher Queries ──────────────────────────────────────")
        with self.driver.session() as s:
            for label, query in EXAMPLE_QUERIES:
                print(f"\n{label}")
                for row in s.run(query):
                    print(" ", dict(row))
        print("────────────────────────────────────────────────────────────────\n")

    def summary(self):
        counts = {
            "Paper nodes":       "MATCH (p:Paper)  RETURN count(p) AS n",
            "Author nodes":      "MATCH (a:Author) RETURN count(a) AS n",
            "Topic nodes":       "MATCH (t:Topic)  RETURN count(t) AS n",
            "AUTHORED_BY edges": "MATCH ()-[:AUTHORED_BY]->() RETURN count(*) AS n",
            "HAS_TOPIC edges":   "MATCH ()-[:HAS_TOPIC]->()   RETURN count(*) AS n",
        }
        print("\n── Graph Summary ───────────────────────────────")
        with self.driver.session() as s:
            for label, q in counts.items():
                n = s.run(q).single()["n"]
                print(f"  {label:<22} {n:>6}")
        print("────────────────────────────────────────────────\n")

    def prune_topics(self, max_papers: int = 20) -> dict:
        """Remove Topic nodes that appear in more than max_papers papers."""
        cypher = """
        MATCH (t:Topic)<-[:HAS_TOPIC]-(p:Paper)
        WITH  t, count(p) AS freq
        WHERE freq > $max_papers
        DETACH DELETE t
        """
        with self.driver.session() as session:
            result  = session.run(cypher, max_papers=max_papers)
            summary = result.consume()
            deleted = {
                "nodes":         summary.counters.nodes_deleted,
                "relationships": summary.counters.relationships_deleted,
            }
        log.info(f"Pruned {deleted['nodes']} over-represented topic(s), "
                 f"{deleted['relationships']} edges removed (threshold: >{max_papers} papers)")
        return deleted

    def close(self):
        self.driver.close()
        self.mongo.client.close()


# ── entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    builder = GraphBuilder()
    try:
        builder.load_all()
        builder.prune_topics()
        builder.summary()
        builder.run_example_queries()
    finally:
        builder.close()

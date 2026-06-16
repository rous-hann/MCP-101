"""
A minimal MCP server that exposes a SQLite database to an AI host.

Tools exposed:
  - list_tables()      -> names of all tables
  - describe_table(t)  -> column schema for one table
  - run_query(sql)     -> run a READ-ONLY SELECT and return rows

Run it:
  pip install mcp
  python sqlite_mcp_server.py          # speaks MCP over stdio

Point an MCP host (e.g. Claude Desktop config) at this script to use it.
"""

import os
import json
import sqlite3
from mcp.server.fastmcp import FastMCP

# 1) Create the server. The name is what the host shows the user.
mcp = FastMCP("sqlite-explorer")

# Absolute path anchored to THIS script's folder. A host (Claude Desktop)
# launches the server with an unknown working directory, so a relative
# "demo.db" fails with "unable to open database file". This always resolves
# to a writable location next to the script.
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db")


def _connect() -> sqlite3.Connection:
    """Open a connection. row_factory=Row lets us read columns by name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_if_empty() -> None:
    """Create a tiny demo schema + rows so the server works out of the box."""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT,
            created_at TEXT
        )
    """)
    # Only insert if the table is empty (idempotent).
    if cur.execute("SELECT count(*) FROM users").fetchone()[0] == 0:
        cur.executemany(
            "INSERT INTO users (name, email, created_at) VALUES (?, ?, ?)",
            [
                ("Ada Lovelace",   "ada@example.com",   "2026-06-10"),
                ("Alan Turing",    "alan@example.com",  "2026-06-12"),
                ("Grace Hopper",   "grace@example.com", "2026-06-14"),
            ],
        )
    conn.commit()
    conn.close()


# 2) Each @mcp.tool() turns a Python function into a callable tool.
#    The docstring + type hints become the schema the model reads.

@mcp.tool()
def list_tables() -> list[str]:
    """List all table names in the database."""
    conn = _connect()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    return [r["name"] for r in rows]


@mcp.tool()
def describe_table(table: str) -> str:
    """Return the column schema (name, type, nullable) for one table."""
    # Guard the identifier: PRAGMA can't be parameterized, so we whitelist
    # against the real table list instead of interpolating raw input.
    if table not in list_tables():
        return f"Error: unknown table '{table}'."
    conn = _connect()
    cols = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    conn.close()
    schema = [
        {"name": c["name"], "type": c["type"], "nullable": not c["notnull"]}
        for c in cols
    ]
    return json.dumps(schema, indent=2)


@mcp.tool()
def run_query(sql: str) -> str:
    """
    Run a READ-ONLY SQL query and return rows as JSON.
    Only a single SELECT (or WITH ... SELECT) statement is allowed.
    """
    cleaned = sql.strip().rstrip(";").lstrip().lower()

    # --- Safety guard (deliberately conservative) ---
    if not (cleaned.startswith("select") or cleaned.startswith("with")):
        return "Error: only SELECT/WITH queries are allowed."
    if ";" in sql.strip().rstrip(";"):
        return "Error: multiple statements are not allowed."
    banned = ("insert", "update", "delete", "drop", "alter",
              "create", "attach", "pragma", "replace")
    if any(f" {kw} " in f" {cleaned} " for kw in banned):
        return "Error: write/DDL keywords are not allowed."

    conn = _connect()
    try:
        rows = conn.execute(sql).fetchmany(200)  # cap result size
        return json.dumps([dict(r) for r in rows], indent=2, default=str)
    except sqlite3.Error as e:
        return f"SQL error: {e}"
    finally:
        conn.close()

@mcp.tool()
def insert_user(name: str, email: str, created_at: str) -> str:
    """Insert one user. created_at should be YYYY-MM-DD."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO users (name, email, created_at) VALUES (?, ?, ?)",
            (name, email, created_at),
        )
        conn.commit()
        return f"Inserted {name}."
    except sqlite3.Error as e:
        return f"SQL error: {e}"
    finally:
        conn.close()

if __name__ == "__main__":
    _seed_if_empty()
    mcp.run()  # default transport is stdio
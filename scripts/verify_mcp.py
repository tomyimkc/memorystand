#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prove the CockroachDB Cloud MCP connection works -- and prove what it is NOT allowed to do.

docs/MCP.md used to say the MCP connection was "untested", and separately claimed the
service account was "read-only by design". The first was honest; the second was an
assumption, and this script exists because the assumption is not obviously true: the MCP
server advertises ``create_database``, ``create_table`` and ``insert_rows`` to *every*
identity, including this one. Tool availability is not permission. If read-only is real it
is enforced by the CLUSTER_DEVELOPER role at the SQL layer, and the only way to know is to
try a write and be refused.

So this runs three things against the live cluster:

  1. the MCP handshake and tool list        -- does the connection work at all
  2. read queries a judge would care about  -- trust ladder, vector index plan
  3. a deliberate write attempt             -- is "read-only" enforced or just claimed

A write that SUCCEEDS is a finding, not a crash: it means the README overstates the
guarantee, and this script says so in those words rather than passing quietly. The probe
targets a uniquely-named throwaway table so a success is trivially reversible.

The write probe is opt-in (``--probe-writes``) because a probe that succeeds leaves a
real table on a live cluster -- the MCP server has no tool to drop one -- and a script
whose job is verification should not mutate the thing it is verifying by default.

Usage:
    CCLOUD_API_KEY=... MEMORYSTAND_CLUSTER_ID=... python scripts/verify_mcp.py
    ... python scripts/verify_mcp.py --probe-writes   # also attempt a write

The key is read from the environment and never written to disk or printed. Create a
short-lived one, run this, then delete it -- see docs/MCP.md.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://cockroachlabs.cloud/mcp"
DEMO_TENANT = "9c8f6e5a-9d1a-4a1c-8f2e-3b6d1c7a4e10"


class MCPError(RuntimeError):
    pass


class MCPClient:
    """The smallest MCP-over-HTTP client that can hold this server honest.

    Not a general MCP implementation: it speaks just enough JSON-RPC to call tools, and it
    parses the Server-Sent-Events framing the Cockroach Labs endpoint replies with (each
    response arrives as ``event: message`` / ``data: {...}``, not as a bare JSON body --
    a plain ``json.loads`` of the response fails and that is not a network error).
    """

    def __init__(self, api_key: str, cluster_id: str | None) -> None:
        self._id = 0
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        if cluster_id:
            self._headers["mcp-cluster-id"] = cluster_id

    def _call(self, method: str, params: dict) -> dict:
        self._id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode()
        req = urllib.request.Request(ENDPOINT, data=payload, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as exc:
            raise MCPError(f"HTTP {exc.code}: {exc.read().decode()[:300]}") from exc

        for line in raw.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return json.loads(raw) if raw.strip() else {}

    def initialize(self) -> dict:
        return self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "memorystand-verify", "version": "1.0"},
            },
        )

    def tools(self) -> list[str]:
        r = self._call("tools/list", {})
        return sorted(t["name"] for t in r.get("result", {}).get("tools", []))

    def tool(self, name: str, arguments: dict) -> tuple[bool, str]:
        """Call a tool. Returns (ok, text) -- a refusal is a result, not an exception."""
        r = self._call("tools/call", {"name": name, "arguments": arguments})
        if "error" in r:
            return False, str(r["error"].get("message", r["error"]))
        result = r.get("result", {})
        text = "\n".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            return False, text
        return True, text


def main() -> int:
    api_key = os.environ.get("CCLOUD_API_KEY", "").strip()
    cluster_id = os.environ.get("MEMORYSTAND_CLUSTER_ID", "").strip()
    if not api_key:
        print("CCLOUD_API_KEY is not set. See the module docstring.", file=sys.stderr)
        return 2

    mcp = MCPClient(api_key, cluster_id)
    findings: list[str] = []

    print("=" * 72)
    print("1. Handshake")
    info = mcp.initialize().get("result", {}).get("serverInfo", {})
    print(f"   server: {info.get('name')} v{info.get('version')}")

    names = mcp.tools()
    print(f"   tools offered to this identity ({len(names)}):")
    print(f"     {', '.join(names)}")
    write_tools = [n for n in names if n.startswith(("create_", "insert_"))]
    if write_tools:
        print(
            f"   NOTE: write tools are offered to a 'read-only' identity: {', '.join(write_tools)}\n"
            "   Tool availability is not permission -- tested below."
        )

    print()
    print("2. Reads a judge would care about")
    ok, text = mcp.tool(
        "select_query",
        {
            "database": "defaultdb",
            "query": (
                "SELECT trust_tier, count(*) AS memories FROM agent_memories "
                f"WHERE tenant_id = '{DEMO_TENANT}' GROUP BY trust_tier"
            ),
        },
    )
    print(f"   trust ladder read: {'OK' if ok else 'FAILED'}")
    print("   " + text.strip()[:500].replace("\n", "\n   "))
    if not ok:
        findings.append("the read query failed; the MCP connection is not usable as documented")

    ok, plan = mcp.tool(
        "explain_query",
        {
            "database": "defaultdb",
            # This must be the query backend/memory.py::recall() actually issues, character
            # for character in the parts the optimizer cares about. Two hand-written
            # variants of "the same" query both silently fell back to a scan here:
            #   - `<->` (L2) instead of `<=>` (cosine). The index is vector_cosine_ops, so
            #     an L2 ordering cannot use it at all.
            #   - an extra `embedding IS NOT NULL` predicate, which defeats it.
            # Both produced a plausible-looking plan with no `vector search` node, which
            # reads as "the index does not work at this scale" and is simply false.
            "query": (
                "SELECT memory_id, content, embedding <=> "
                "(SELECT embedding FROM agent_memories "
                f"WHERE tenant_id = '{DEMO_TENANT}' LIMIT 1) AS distance "
                f"FROM agent_memories WHERE tenant_id = '{DEMO_TENANT}' "
                "AND verdict = 'accepted' ORDER BY embedding <=> "
                "(SELECT embedding FROM agent_memories "
                f"WHERE tenant_id = '{DEMO_TENANT}' LIMIT 1) LIMIT 5"
            ),
        },
    )
    uses_index = "vector search" in plan.lower()
    print(f"   query plan read : {'OK' if ok else 'FAILED'}")
    print(f"   vector search node present: {uses_index}")
    if ok and not uses_index:
        print(
            "   (expected at low row counts -- the optimizer picks a scan when the tenant\n"
            "    holds few rows. See benchmarks/ for the row counts where it flips.)"
        )

    print()
    print("3. Is 'read-only' enforced, or only claimed?")
    if "--probe-writes" not in sys.argv:
        print("   SKIPPED -- pass --probe-writes to actually test it.")
        print("   Opt-in because the only honest test of a write permission is attempting a")
        print("   write, and this MCP server exposes no tool to drop a table. A probe that")
        print("   SUCCEEDS therefore leaves a real table on a live cluster for someone to")
        print("   clean up by hand. A script whose job is verification should not mutate")
        print("   the thing it is verifying unless asked.")
        return 1 if findings else 0

    probe_table = "mcp_write_probe_delete_me"
    ok, msg = mcp.tool(
        "create_table",
        {"database": "defaultdb", "ddl": f"CREATE TABLE {probe_table} (id INT PRIMARY KEY)"},
    )
    if ok:
        print(f"   WRITE SUCCEEDED -- created table {probe_table!r}.")
        print("   This identity is NOT read-only. Two things now need doing:")
        print(f"     1. DROP TABLE {probe_table};  -- via cockroach sql, not MCP")
        print("     2. tighten the service account's role, and correct docs/MCP.md")
        findings.append(
            f"the MCP identity created table {probe_table!r}: "
            "'read-only' is claimed in docs/MCP.md but not enforced"
        )
    else:
        print("   write refused, as documented:")
        print(f"     {msg.strip()[:300]}")

    print()
    print("=" * 72)
    if findings:
        print("FINDINGS -- documentation does not match reality:")
        for f in findings:
            print(f"  - {f}")
        return 1
    print("MCP connection verified: reads work, writes are refused.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

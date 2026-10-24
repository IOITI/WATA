"""
The table ``mcp_token`` in memory, answering the statements of
src/admin/mcp_token_store.py: for the tests of the store and of watamcptoken.
The same statements run against PostgreSQL in tests/test_infrastructure_live.py.
"""


class TokenTable:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.closed = False
        self.failing = None

    def _check(self):
        if self.failing is not None:
            raise self.failing

    async def execute(self, query, *args):
        self._check()
        query = " ".join(query.split())
        if query.startswith("INSERT INTO mcp_token"):
            if "revoked_at)" in query:
                token_id, name, sha256, created_at, expires_at, revoked_at = args
            else:
                (token_id, name, sha256, created_at, expires_at), revoked_at = args, None
            if token_id in self.rows:
                return "INSERT 0 0"
            self.rows[token_id] = {"id": token_id, "name": name, "sha256": sha256, "created_at": created_at,
                                   "expires_at": expires_at, "revoked_at": revoked_at, "last_used_at": None}
            return "INSERT 0 1"
        if query.startswith("UPDATE mcp_token SET last_used_at"):
            token_id, moment = args
            if token_id in self.rows:
                self.rows[token_id]["last_used_at"] = moment
            return "UPDATE 1"
        if "CREATE TABLE IF NOT EXISTS" in query:
            return "CREATE TABLE"
        raise AssertionError(f"Not a statement of the token store: {query}")

    async def fetchval(self, query, *args):
        self._check()
        assert " ".join(query.split()) == "SELECT count(*) FROM mcp_token", query
        return len(self.rows)

    async def fetch(self, query, *args):
        self._check()
        query = " ".join(query.split())
        if query.startswith("SELECT id, name, sha256"):
            return sorted((dict(row) for row in self.rows.values()), key=lambda row: row["created_at"])
        if query.startswith("UPDATE mcp_token SET revoked_at"):
            name_or_id, now = args
            found = []
            for row in self.rows.values():
                if name_or_id in (row["name"], row["id"]) and row["revoked_at"] is None and row["expires_at"] > now:
                    row["revoked_at"] = now
                    found.append(dict(row))
            return found
        raise AssertionError(f"Not a statement of the token store: {query}")

    async def close(self):
        self.closed = True

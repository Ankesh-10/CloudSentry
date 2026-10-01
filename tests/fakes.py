"""In-memory stand-in for the supabase-py query builder.

Implements the subset of PostgREST semantics the backend uses: filters actually
filter, updates only touch matching rows, upserts honour on_conflict, the
`resources(*)` embed is resolved from the foreign key, and the partial unique
indexes from migration 007 are enforced so race-guard code paths are exercised.
"""
import copy
import uuid
from datetime import datetime


class FakeAPIError(Exception):
    pass


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


def _cmp_val(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


UNIQUE_PARTIAL = {
    "anomalies": [(("resource_id", "anomaly_type"), lambda r: r.get("status") == "active")],
    "optimization_actions": [
        (("resource_id", "action_type"), lambda r: r.get("status") in ("pending", "pending_approval", "approved", "executing"))
    ],
}


class _Not:
    def __init__(self, q):
        self._q = q

    def in_(self, col, values):
        vals = set(values)
        return self._q._filter(lambda r: r.get(col) not in vals)

    def is_(self, col, value):
        if value == "null":
            return self._q._filter(lambda r: r.get(col) is not None)
        raise NotImplementedError


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.filters = []
        self.op = "select"
        self.payload = None
        self.embed = False
        self.want_count = False
        self._order = []
        self._limit = None
        self._range = None
        self.on_conflict = None

    # -- builders ------------------------------------------------------------
    def select(self, cols="*", count=None):
        self.op = "select"
        self.embed = "resources(" in cols
        self.want_count = count == "exact"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict="", **_):
        self.op, self.payload, self.on_conflict = "upsert", payload, on_conflict
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _filter(self, fn):
        self.filters.append(fn)
        return self

    def eq(self, col, val):
        return self._filter(lambda r: _cmp_val(r.get(col)) == _cmp_val(val))

    def neq(self, col, val):
        return self._filter(lambda r: _cmp_val(r.get(col)) != _cmp_val(val))

    def in_(self, col, values):
        vals = set(values)
        return self._filter(lambda r: r.get(col) in vals)

    def is_(self, col, value):
        if value == "null":
            return self._filter(lambda r: r.get(col) is None)
        raise NotImplementedError

    def gte(self, col, val):
        return self._filter(lambda r: r.get(col) is not None and str(_cmp_val(r.get(col))) >= str(_cmp_val(val)))

    def lt(self, col, val):
        return self._filter(lambda r: r.get(col) is not None and str(_cmp_val(r.get(col))) < str(_cmp_val(val)))

    @property
    def not_(self):
        return _Not(self)

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    # -- execution -----------------------------------------------------------
    def _rows(self):
        return self.db.tables.setdefault(self.table, [])

    def _match(self, row):
        return all(f(row) for f in self.filters)

    def _check_unique(self, candidate, exclude=None):
        for cols, pred in UNIQUE_PARTIAL.get(self.table, []):
            if not pred(candidate):
                continue
            key = tuple(candidate.get(c) for c in cols)
            for r in self._rows():
                if r is exclude:
                    continue
                if pred(r) and tuple(r.get(c) for c in cols) == key:
                    raise FakeAPIError(f"duplicate key on {self.table}{cols}")

    def execute(self):
        self.db.calls.append((self.table, self.op))
        if self.db.fail_tables.get(self.table):
            raise FakeAPIError(f"simulated failure on {self.table}")
        rows = self._rows()

        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in payload:
                row = copy.deepcopy(p)
                row.setdefault("id", str(uuid.uuid4()))
                self._check_unique(row)
                rows.append(row)
                out.append(copy.deepcopy(row))
            return _Result(out)

        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            keys = [k.strip() for k in (self.on_conflict or "id").split(",")]
            out = []
            for p in payload:
                existing = next((r for r in rows if all(r.get(k) == p.get(k) for k in keys)), None)
                if existing is not None:
                    existing.update(copy.deepcopy(p))
                    out.append(copy.deepcopy(existing))
                else:
                    row = copy.deepcopy(p)
                    row.setdefault("id", str(uuid.uuid4()))
                    rows.append(row)
                    out.append(copy.deepcopy(row))
            return _Result(out)

        matched = [r for r in rows if self._match(r)]

        if self.op == "update":
            out = []
            for r in matched:
                candidate = {**r, **self.payload}
                self._check_unique(candidate, exclude=r)
                r.update(copy.deepcopy(self.payload))
                out.append(copy.deepcopy(r))
            return _Result(out)

        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return _Result([copy.deepcopy(r) for r in matched])

        for col, desc in reversed(self._order):
            matched.sort(key=lambda r: (r.get(col) is None, str(_cmp_val(r.get(col)))), reverse=desc)
        total = len(matched)
        if self._range:
            matched = matched[self._range[0]: self._range[1] + 1]
        if self._limit is not None:
            matched = matched[: self._limit]
        out = []
        for r in matched:
            row = copy.deepcopy(r)
            if self.embed:
                res = next((x for x in self.db.tables.get("resources", []) if x["id"] == r.get("resource_id")), None)
                row["resources"] = copy.deepcopy(res)
            out.append(row)
        return _Result(out, count=total if self.want_count else None)


class FakeSupabase:
    def __init__(self, tables=None):
        self.tables = {k: [copy.deepcopy(r) for r in v] for k, v in (tables or {}).items()}
        self.calls = []
        self.fail_tables = {}

    def table(self, name):
        return FakeQuery(self, name)

    def rows(self, name):
        return self.tables.setdefault(name, [])

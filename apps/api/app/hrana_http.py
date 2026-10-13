"""Remote libsql over HTTP (Hrana v2). No embedded replica and no sync_url.

Turso and sqld speak this protocol at ``/v2/pipeline``. A ``libsql://`` URL
is opened as HTTPS. ``?tls=0`` stays on HTTP so a local sqld can be used.
"""

from __future__ import annotations

import base64
import sqlite3
import urllib.parse

import httpx

_MIN_INTEGER = -(2**63)
_MAX_INTEGER = 2**63 - 1


def http_base(url: str) -> str:
    """Map a remote database URL onto its HTTP API. ``file:`` is refused."""
    parsed = urllib.parse.urlparse(url.strip())
    scheme = (parsed.scheme or "").lower()
    query = urllib.parse.parse_qs(parsed.query)
    tls = (query.get("tls") or [None])[0]
    if scheme == "libsql":
        scheme = "http" if tls == "0" else "https"
    elif scheme == "wss":
        scheme = "https"
    elif scheme == "ws":
        scheme = "http"
    elif scheme not in {"http", "https"}:
        raise RuntimeError(
            "AYVEN_DB_URL must be a remote libsql URL (libsql://, https://, or http://). "
            "Embedded replica sync is not used."
        )
    if not parsed.netloc:
        raise RuntimeError("AYVEN_DB_URL has no host")
    path = parsed.path or ""
    if path.endswith("/"):
        path = path[:-1]
    return urllib.parse.urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _value_to_proto(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        if value < _MIN_INTEGER or value > _MAX_INTEGER:
            raise OverflowError("integer exceeds signed 64-bit SQLite range")
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    if isinstance(value, str):
        return {"type": "text", "value": value}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode()}
    return {"type": "text", "value": str(value)}


def _value_from_proto(value):
    kind = value.get("type")
    if kind == "null":
        return None
    if kind == "integer":
        return int(value["value"])
    if kind == "float":
        return float(value["value"])
    if kind == "text":
        return str(value.get("value") or "")
    if kind == "blob":
        return base64.b64decode(value.get("base64") or "")
    raise sqlite3.OperationalError(f"unknown libsql value type {kind!r}")


class _HranaCursor:
    def __init__(self, result: dict):
        cols = result.get("cols") or []
        self._keys = [col.get("name") or "" for col in cols]
        self._rows = []
        for proto_row in result.get("rows") or []:
            values = tuple(_value_from_proto(item) for item in proto_row)
            self._rows.append(_MappedRow(self._keys, values))
        self._index = 0
        affected = result.get("affected_row_count")
        self.rowcount = int(affected) if affected is not None else -1

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class _MappedRow(dict):
    """row['id'], row[0], and dict(row) for a remote Hrana row."""

    def __init__(self, keys, values):
        super().__init__(zip(keys, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class HranaConnection:
    """sqlite-shaped connection. One HTTP stream, held open only during a transaction."""

    def __init__(self, url: str, auth_token: str):
        self._base = http_base(url)
        self._token = auth_token
        self._baton = None
        self._tx = False
        self._client = httpx.Client(timeout=60)

    def _pipeline(self, requests: list, *, close: bool) -> list:
        body_requests = list(requests)
        if close:
            body_requests.append({"type": "close"})
        body = {"requests": body_requests}
        if self._baton:
            body["baton"] = self._baton
        try:
            response = self._client.post(
                f"{self._base}/v2/pipeline",
                headers={"Authorization": f"Bearer {self._token}"},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise sqlite3.OperationalError(f"libsql HTTP request failed: {exc}") from exc
        if response.status_code >= 400:
            raise sqlite3.OperationalError(
                f"libsql HTTP {response.status_code}: {response.text[:300]}"
            )
        payload = response.json()
        base_url = payload.get("base_url")
        if isinstance(base_url, str) and base_url.startswith(("http://", "https://")):
            self._base = base_url.rstrip("/")
        if close or payload.get("baton") is None:
            self._baton = None
        else:
            self._baton = payload.get("baton")
        parsed = []
        for item in payload.get("results") or []:
            if item.get("type") == "error":
                message = (item.get("error") or {}).get("message") or "libsql statement failed"
                raise sqlite3.OperationalError(message)
            parsed.append(item.get("response") or {})
        return parsed

    def _stmt(self, sql: str, params) -> dict:
        args = [_value_to_proto(value) for value in (params or ())]
        return {"sql": sql, "args": args, "named_args": [], "want_rows": True}

    def execute(self, sql, params=()):
        head = ""
        stripped = (sql or "").lstrip()
        if stripped:
            head = stripped.split(None, 1)[0].upper()
        if head == "BEGIN":
            self._tx = True
        closing = head in {"COMMIT", "ROLLBACK", "END"} or not self._tx
        responses = self._pipeline([{"type": "execute", "stmt": self._stmt(sql, params)}], close=closing)
        if head in {"COMMIT", "ROLLBACK", "END"}:
            self._tx = False
        result = {}
        for item in responses:
            if item.get("type") == "execute":
                result = item.get("result") or {}
        return _HranaCursor(result)

    def executemany(self, sql, seq):
        last = None
        for params in seq:
            last = self.execute(sql, params)
        return last

    def executescript(self, sql: str):
        text = sql or ""
        if not text.strip():
            return self
        closing = not self._tx
        self._pipeline([{"type": "sequence", "sql": text}], close=closing)
        return self

    def commit(self):
        if self._tx:
            self.execute("COMMIT")

    def rollback(self):
        if self._tx or self._baton:
            try:
                self.execute("ROLLBACK")
            except sqlite3.OperationalError:
                self._tx = False
                self._close_stream()

    def _close_stream(self):
        if not self._baton:
            return
        try:
            self._pipeline([], close=True)
        except sqlite3.OperationalError:
            self._baton = None

    def close(self):
        self._close_stream()
        self._client.close()

"""Key/value storage for OAuth state: clients, pending requests, codes, tokens.

Every item is a JSON-able dict under a string key with an optional expiry.
DynamoDB in production (one table, TTL attribute), a dict in tests.
"""

from __future__ import annotations

import time
from typing import Any, Protocol


class OAuthStore(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...
    def put(self, key: str, item: dict[str, Any], ttl_seconds: int | None = None) -> None: ...
    def delete(self, key: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[dict[str, Any], float | None]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._items.get(key)
        if not entry:
            return None
        item, exp = entry
        if exp is not None and exp < time.time():
            self._items.pop(key, None)
            return None
        return dict(item)

    def put(self, key: str, item: dict[str, Any], ttl_seconds: int | None = None) -> None:
        exp = time.time() + ttl_seconds if ttl_seconds else None
        self._items[key] = (dict(item), exp)

    def delete(self, key: str) -> None:
        self._items.pop(key, None)


class DynamoStore:
    """Single-table store. Partition key `pk` (string); `ttl` epoch seconds for expiry.

    Dynamo's TTL sweeper lags by up to ~48h, so expiry is ALSO enforced on read.
    """

    def __init__(self, table_name: str, region: str = "us-east-1") -> None:
        import boto3

        self._table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    def get(self, key: str) -> dict[str, Any] | None:
        resp = self._table.get_item(Key={"pk": key}, ConsistentRead=True)
        item = resp.get("Item")
        if not item:
            return None
        ttl = item.get("ttl")
        if ttl is not None and int(ttl) < int(time.time()):
            return None
        data = item.get("data")
        # DynamoDB hands numbers back as Decimal, which json.dumps refuses.
        # The store's contract is plain JSON-able values, so convert here.
        return _from_dynamo(data) if isinstance(data, dict) else None

    def put(self, key: str, item: dict[str, Any], ttl_seconds: int | None = None) -> None:
        record: dict[str, Any] = {"pk": key, "data": _dynamo_safe(item)}
        if ttl_seconds:
            record["ttl"] = int(time.time()) + int(ttl_seconds)
        self._table.put_item(Item=record)

    def delete(self, key: str) -> None:
        self._table.delete_item(Key={"pk": key})


def _from_dynamo(value: Any) -> Any:
    """Decimal → int/float, recursively."""
    from decimal import Decimal

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _dynamo_safe(value: Any) -> Any:
    """DynamoDB rejects floats and empty strings inside maps; normalise."""
    from decimal import Decimal

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dynamo_safe(v) for v in value]
    if value == "":
        return None
    return value

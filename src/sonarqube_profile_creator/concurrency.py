from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar


T = TypeVar("T")
R = TypeVar("R")


def bounded_parallel_map(items: Iterable[T], worker: Callable[[T], R], max_workers: int) -> list[R]:
    item_list = list(items)
    if not item_list:
        return []
    workers = max(1, min(max_workers, len(item_list)))
    if workers == 1:
        return [worker(item) for item in item_list]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(worker, item_list))


def client_read_workers(client: object, item_count: int, default: int = 8) -> int:
    return _client_workers(client, "max_read_workers", item_count, default)


def client_write_workers(client: object, item_count: int, default: int = 4) -> int:
    return _client_workers(client, "max_write_workers", item_count, default)


def _client_workers(client: object, attr: str, item_count: int, default: int) -> int:
    try:
        configured = int(getattr(client, attr, default) or default)
    except (TypeError, ValueError):
        configured = default
    return max(1, min(configured, max(item_count, 1)))

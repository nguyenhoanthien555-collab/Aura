"""Helpers for inspecting routes registered through FastAPI routers.

Recent FastAPI versions keep included routers as lightweight wrappers in
``app.routes``.  Those wrappers have no ``path`` of their own; the actual
routes remain on ``original_router`` until the application builds its
effective routing table.  Security checks must therefore walk both normal
mounted routes and included-router wrappers instead of assuming every
entry is an ``APIRoute``.
"""

from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True)
class EnumeratedRoute:
    """The effective path and methods of one concrete HTTP route."""

    path: str
    methods: frozenset[str]
    route: object


def iter_http_routes(application_or_routes) -> Iterator[EnumeratedRoute]:
    """Yield concrete HTTP routes recursively, including lazy routers."""

    routes = getattr(application_or_routes, "routes", application_or_routes)
    yield from _walk_routes(routes or (), prefix="", seen=set())


def _walk_routes(
    routes: Iterable[object],
    *,
    prefix: str,
    seen: set[tuple[int, str]],
) -> Iterator[EnumeratedRoute]:
    for route in routes:
        marker = (id(route), prefix)
        if marker in seen:
            continue
        seen.add(marker)

        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is not None and methods is not None:
            yield EnumeratedRoute(
                path=_join_paths(prefix, path),
                methods=frozenset(methods),
                route=route,
            )
            continue

        # Starlette mounts and older router containers expose ``routes``.
        nested = getattr(route, "routes", None)
        nested_prefix = _join_paths(prefix, getattr(route, "path", ""))

        # FastAPI's lazy _IncludedRouter exposes the source router here.
        if nested is None:
            original = getattr(route, "original_router", None)
            nested = getattr(original, "routes", None)
            context = getattr(route, "include_context", None)
            nested_prefix = _join_paths(
                prefix, getattr(context, "prefix", "")
            )

        if nested is not None:
            yield from _walk_routes(
                nested, prefix=nested_prefix, seen=seen
            )


def _join_paths(prefix: str, path: str) -> str:
    if not prefix:
        return path or ""
    if not path:
        return prefix
    return f"{prefix.rstrip('/')}/{path.lstrip('/')}"

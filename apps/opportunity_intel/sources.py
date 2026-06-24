"""Source registry and query-pack loading."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from apps.opportunity_intel.contracts import (
    QUERY_PACK_CONTRACT_VERSION,
    SOURCE_REGISTRY_CONTRACT_VERSION,
    JsonObject,
    OpportunityContractError,
    QueryDefinition,
    QueryPack,
    SourceDefinition,
    SourceRegistry,
)

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_SOURCE_REGISTRY_PATH = DATA_DIR / "source_registry.v1.json"
DEFAULT_QUERY_PACK_PATH = DATA_DIR / "query_pack.v1.json"


def load_source_registry(path: Path = DEFAULT_SOURCE_REGISTRY_PATH) -> SourceRegistry:
    payload = _load_json_object(path)
    contract_version = _read_contract_version(payload, expected=SOURCE_REGISTRY_CONTRACT_VERSION)
    sources_payload = payload.get("sources")
    if not isinstance(sources_payload, list):
        raise OpportunityContractError("source registry requires a sources list")
    sources = tuple(
        SourceDefinition.from_mapping(_ensure_json_object(item)) for item in sources_payload
    )
    _require_unique(source.source_id for source in sources)
    return SourceRegistry(contract_version=contract_version, sources=sources)


def load_query_pack(path: Path = DEFAULT_QUERY_PACK_PATH) -> QueryPack:
    payload = _load_json_object(path)
    contract_version = _read_contract_version(payload, expected=QUERY_PACK_CONTRACT_VERSION)
    queries_payload = payload.get("queries")
    if not isinstance(queries_payload, list):
        raise OpportunityContractError("query pack requires a queries list")
    queries = tuple(
        QueryDefinition.from_mapping(_ensure_json_object(item)) for item in queries_payload
    )
    _require_unique(query.query_id for query in queries)
    return QueryPack(contract_version=contract_version, queries=queries)


def validate_registry_against_queries(registry: SourceRegistry, query_pack: QueryPack) -> None:
    query_ids = {query.query_id for query in query_pack.queries}
    source_ids = {source.source_id for source in registry.sources}
    for source in registry.sources:
        missing_queries = sorted(set(source.query_ids) - query_ids)
        if missing_queries:
            raise OpportunityContractError(
                f"{source.source_id} references unknown queries: {', '.join(missing_queries)}"
            )
    for query in query_pack.queries:
        missing_sources = sorted(set(query.source_ids) - source_ids)
        if missing_sources:
            raise OpportunityContractError(
                f"{query.query_id} references unknown sources: {', '.join(missing_sources)}"
            )


def _load_json_object(path: Path) -> JsonObject:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return _ensure_json_object(payload)


def _ensure_json_object(value: object) -> JsonObject:
    if not isinstance(value, dict):
        raise OpportunityContractError("expected JSON object")
    return cast(JsonObject, value)


def _read_contract_version(payload: JsonObject, *, expected: str) -> str:
    value = payload.get("contract_version")
    if value != expected:
        raise OpportunityContractError(f"expected contract_version {expected}")
    return expected


def _require_unique(values: Iterable[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise OpportunityContractError(f"duplicate identifier: {value}")
        seen.add(value)

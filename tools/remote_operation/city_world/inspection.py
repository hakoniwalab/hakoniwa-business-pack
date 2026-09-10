"""PLATEAU catalog inspection adapter owned by the City World job layer."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from .protocol import canonical_sha256, validate_request


API_BASE_URL = "https://api.plateauview.mlit.go.jp"
FEATURES = {
    "building": ("bldg", 1),
    "terrain": ("dem", 1),
    "road": ("tran", 1),
    "road_markings": ("frn", 1),
    "bridge": ("brid", 1),
}


class CityWorldInspectionError(RuntimeError):
    pass


def resolve_envsim_root(explicit: Path | None = None) -> Path:
    if explicit is not None:
        root = explicit.resolve()
    elif os.environ.get("HAKONIWA_ENVSIM_ROOT"):
        root = Path(os.environ["HAKONIWA_ENVSIM_ROOT"]).resolve()
    else:
        root = (Path(__file__).resolve().parents[4] / "hakoniwa-envsim").resolve()
    module_path = root / "tools" / "plateau_citygml.py"
    if not module_path.is_file():
        raise CityWorldInspectionError(
            f"hakoniwa-envsim PLATEAU client not found: {module_path}"
        )
    return root


def load_envsim_plateau_client(root: Path | None = None) -> ModuleType:
    module_path = resolve_envsim_root(root) / "tools" / "plateau_citygml.py"
    spec = importlib.util.spec_from_file_location("hakoniwa_envsim_plateau_citygml", module_path)
    if spec is None or spec.loader is None:
        raise CityWorldInspectionError(f"cannot load Envsim PLATEAU client: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload_sha256(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _capability(name: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    if not files:
        return {
            "dataset_status": "not_available",
            "generation_status": "scoped_out",
            "max_lod": None,
            "source_file_count": 0,
            "reason": "dataset is not available in the selected bbox",
        }
    max_lod = max(int(item.get("max_lod", 0)) for item in files)
    generation_status = "candidate"
    reason = None
    if name in {"road_markings", "bridge"} and max_lod < 3:
        generation_status = "scoped_out"
        reason = "LOD3 geometry required by the current generator is not available"
    return {
        "dataset_status": "available",
        "generation_status": generation_status,
        "max_lod": max_lod,
        "source_file_count": len(files),
        "reason": reason,
    }


def _files_in_query_meshes(
    files: list[dict[str, Any]], query_mesh_codes: set[str],
) -> list[dict[str, Any]]:
    """Remove second-mesh catalog spillover using each file's third-mesh code."""
    return [
        item for item in files
        if str(item.get("code", ""))[:8] in query_mesh_codes
    ]


def inspect_request(
    request: dict[str, Any],
    *,
    plateau_client: ModuleType | None = None,
    dataset_catalog: dict[str, Any] | None = None,
    fetched_at: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Inspect one bounded selection without downloading CityGML assets."""

    request = validate_request(request)
    client = plateau_client or load_envsim_plateau_client()
    clock = fetched_at or (lambda: datetime.now(timezone.utc))
    center = request["selection"]["center"]
    extent = request["selection"]["half_extent_m"]
    bbox = client.bounding_box(
        center["latitude"], center["longitude"],
        extent["north_south"], extent["east_west"],
    )
    query_meshes = []
    for code in client.third_mesh_codes(bbox):
        west, south, east, north = client.third_mesh_bounds(code)
        query_meshes.append({
            "code": code,
            "bbox": {"west": west, "south": south, "east": east, "north": north},
        })
    query_mesh_codes = {item["code"] for item in query_meshes}

    national = dataset_catalog or client.request_dataset_catalog(API_BASE_URL)
    def inspect_feature(
        feature_type: str, min_lod: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        mesh_level = 2 if feature_type == "brid" else 3
        url = client.search_url(API_BASE_URL, feature_type, bbox, mesh_level=mesh_level)
        payload = client.request_catalog(
            url,
            # A catalog 404 means that the selected mesh has no file for this
            # feature.  It is a valid capability result, not a Worker failure.
            allow_not_found=True,
        )
        files = client.select_files(
            payload, feature_type, request["year"], allow_empty=True, min_lod=min_lod,
        )
        if feature_type == "brid":
            files = _files_in_query_meshes(files, query_mesh_codes)
        return payload, files

    # The five feature catalogs are independent HTTP requests.  Keep a small,
    # fixed upper bound so a larger bbox does not multiply their latency while
    # avoiding unbounded pressure on the public API.
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(FEATURES), thread_name_prefix="plateau-capability",
    ) as executor:
        futures = {
            component: executor.submit(inspect_feature, feature_type, min_lod)
            for component, (feature_type, min_lod) in FEATURES.items()
        }
        payloads: dict[str, dict[str, Any]] = {}
        selected: dict[str, list[dict[str, Any]]] = {}
        # Read futures in contract order to keep deterministic output maps.
        for component, (feature_type, _) in FEATURES.items():
            payload, files = futures[component].result()
            payloads[feature_type] = payload
            selected[component] = files

    all_files: dict[str, dict[str, Any]] = {}
    for files in selected.values():
        for item in files:
            all_files[item["url"]] = item
    city_rows: dict[str, dict[str, Any]] = {}
    for item in all_files.values():
        city_rows[item["city_code"]] = {
            "city_code": item["city_code"],
            "city": item["city_name"],
            "year": item["year"],
            "spec": item["spec"],
        }
    city_rows = dict(sorted(city_rows.items()))
    warnings: list[str] = []
    national_codes = {
        str(item.get("city_code", ""))
        for item in national.get("citygml", [])
        if isinstance(item, dict)
    }
    missing_catalog_codes = sorted(set(city_rows) - national_codes)
    if missing_catalog_codes:
        warnings.append(
            "bbox catalog returned municipalities absent from plateau-datasets: "
            + ", ".join(missing_catalog_codes)
        )

    capabilities = {
        name: _capability(name, files) for name, files in selected.items()
    }
    required = ("building", "terrain", "road")
    available = all(
        capabilities[name]["dataset_status"] == "available" for name in required
    )
    reason = None
    if not available:
        missing = [
            name for name in required
            if capabilities[name]["dataset_status"] != "available"
        ]
        reason = "required PLATEAU components are unavailable: " + ", ".join(missing)

    snapshot_value = {
        "national": national,
        "bbox": payloads,
    }
    return {
        "schema_version": 1,
        "status": "available" if available else "unavailable",
        "request_sha256": canonical_sha256(request),
        "bbox": {
            "west": bbox[0], "south": bbox[1], "east": bbox[2], "north": bbox[3],
        },
        "query_meshes": query_meshes,
        "catalog_snapshot": {
            "api_base_url": API_BASE_URL,
            "fetched_at": clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "response_sha256": _payload_sha256(snapshot_value),
        },
        "municipalities": list(city_rows.values()) if available else [],
        "capabilities": capabilities,
        "source_file_count": len(all_files) if available else 0,
        "estimated_download_bytes": (
            sum(int(item.get("file_size", 0)) for item in all_files.values())
            if available else 0
        ),
        "reason": reason,
        "warnings": warnings,
    }


class PlateauSelectionInspector:
    """Reusable inspector that caches the large nationwide catalog per Worker."""

    def __init__(self, plateau_client: ModuleType | None = None) -> None:
        self._client = plateau_client or load_envsim_plateau_client()
        self._dataset_catalog: dict[str, Any] | None = None

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._dataset_catalog is None:
            self._dataset_catalog = self._client.request_dataset_catalog(API_BASE_URL)
        return inspect_request(
            request,
            plateau_client=self._client,
            dataset_catalog=self._dataset_catalog,
        )

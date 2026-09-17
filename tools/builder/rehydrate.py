"""
Dynamic Tool Rehydration Module for AURA 2.0 (Phase 5B.1).

Rehydrates active dynamic tools from SQLite ToolProvenanceRecord into ToolRegistry
on server/runtime startup.

Enforces:
1. Lifecycle state validation (only ACTIVE tools are rehydrated).
2. Cryptographic digest verification (SHA-256 source integrity).
3. Current deterministic AST validation and protocol compatibility.
4. Clean failure isolation (one corrupt record does not block startup or other tools).
5. Dynamic authorization gate in registry/executor.
"""

import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from core.logger import logger
from memory.models import ToolProvenanceRecord, timestamp_now
from tools.base import ToolProtocol, ToolRisk
from tools.builder.builder import ToolBuilder, ToolLifecycleState
from tools.builder.manifest import ToolManifest
from tools.builder.validator import ToolValidator
from tools.registry import ToolRegistry


def rehydrate_active_tools(
    registry: ToolRegistry,
    session_factory: Optional[Callable] = None,
    validator: Optional[ToolValidator] = None,
) -> Dict[str, Any]:
    """
    Rehydrate ACTIVE dynamic tools from SQLite into the provided ToolRegistry.

    Returns a summary dictionary with counts and details of rehydrated
    and rejected tools.
    """
    if session_factory is None:
        try:
            from memory.sqlite import SessionLocal, init_task_tables
            init_task_tables()
            session_factory = SessionLocal
        except Exception as err:
            logger.warning("Dynamic tool rehydration skipped: database session factory unavailable (%s)", err)
            return {"rehydrated": [], "rejected": [], "total_scanned": 0, "error": str(err)}

    val = validator or ToolValidator()
    builder = ToolBuilder(registry=registry, validator=val)

    stats: Dict[str, Any] = {
        "rehydrated": [],
        "rejected": [],
        "total_scanned": 0,
    }

    try:
        with session_factory() as session:
            from sqlalchemy import select
            stmt = (
                select(ToolProvenanceRecord)
                .order_by(ToolProvenanceRecord.name.asc(), ToolProvenanceRecord.version.asc())
            )
            records = list(session.scalars(stmt).all())
    except Exception as query_err:
        logger.warning("Dynamic tool rehydration failed to query provenance records: %s", query_err)
        stats["error"] = str(query_err)
        return stats

    if not records:
        logger.debug("Dynamic tool rehydration: no provenance records found.")
        return stats

    # Group records by tool name
    records_by_name: Dict[str, List[ToolProvenanceRecord]] = {}
    for r in records:
        records_by_name.setdefault(r.name, []).append(r)

    stats["total_scanned"] = len(records_by_name)
    logger.info("Dynamic tool rehydration started: %d candidate tools found in provenance", len(records_by_name))

    for name, tool_records in records_by_name.items():
        try:
            # 1. Check if tool was revoked
            latest_record = tool_records[-1]
            if latest_record.status == ToolLifecycleState.REVOKED.value:
                logger.info("Dynamic tool '%s' was revoked; skipping rehydration", name)
                stats["rejected"].append({"name": name, "version": latest_record.version, "reason": "REVOKED"})
                continue

            # 2. Find the active version
            # If the latest record was ROLLED_BACK, search backwards for the prior ACTIVE version
            active_candidate = None
            if latest_record.status == ToolLifecycleState.ACTIVE.value:
                active_candidate = latest_record
            else:
                # Look backwards for the most recent ACTIVE record
                for r in reversed(tool_records):
                    if r.status == ToolLifecycleState.ACTIVE.value:
                        active_candidate = r
                        break

            if active_candidate is None:
                logger.debug("Dynamic tool '%s' has no ACTIVE version (latest status: %s); skipping", name, latest_record.status)
                stats["rejected"].append({"name": name, "version": latest_record.version, "reason": f"STATUS_{latest_record.status}"})
                continue

            rec = active_candidate

            # 3. Source code integrity check
            if not rec.source_code or not rec.source_code.strip():
                logger.warning("Dynamic tool '%s' v%d has empty source code; rejecting", name, rec.version)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": "EMPTY_SOURCE"})
                continue

            expected_digest = hashlib.sha256(rec.source_code.strip().encode("utf-8")).hexdigest()
            if rec.source_digest and rec.source_digest != expected_digest:
                logger.warning(
                    "Dynamic tool '%s' v%d digest mismatch: expected %s, got %s; rejecting",
                    name, rec.version, expected_digest, rec.source_digest,
                )
                stats["rejected"].append({"name": name, "version": rec.version, "reason": "DIGEST_MISMATCH"})
                continue

            # 4. Manifest parsing
            try:
                manifest_data = json.loads(rec.manifest_json or "{}")
                manifest = ToolManifest.from_dict(manifest_data)
            except Exception as m_err:
                logger.warning("Dynamic tool '%s' v%d manifest corrupt: %s; rejecting", name, rec.version, m_err)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": f"CORRUPT_MANIFEST: {m_err}"})
                continue

            manifest.source_code = rec.source_code
            manifest.source_digest = expected_digest

            # 5. Deterministic validation (AST security and structural check)
            report = val.validate(manifest)
            if not report.passed:
                logger.warning("Dynamic tool '%s' v%d failed deterministic validation: %s; rejecting", name, rec.version, report.errors)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": f"VALIDATION_FAILED: {report.errors}"})
                continue

            # 6. Tool instantiation and protocol check
            try:
                tool_instance = builder.instantiate_tool(manifest)
            except Exception as inst_err:
                logger.warning("Dynamic tool '%s' v%d failed instantiation: %s; rejecting", name, rec.version, inst_err)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": f"INSTANTIATION_FAILED: {inst_err}"})
                continue

            if not isinstance(tool_instance, ToolProtocol):
                logger.warning("Dynamic tool '%s' v%d does not satisfy ToolProtocol; rejecting", name, rec.version)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": "NOT_TOOL_PROTOCOL"})
                continue

            if not isinstance(tool_instance.risk, ToolRisk):
                logger.warning("Dynamic tool '%s' v%d risk is invalid; rejecting", name, rec.version)
                stats["rejected"].append({"name": name, "version": rec.version, "reason": "INVALID_RISK"})
                continue

            # 7. Register into ToolRegistry with provenance
            provenance = {
                "gap_id": rec.gap_id,
                "name": rec.name,
                "version": rec.version,
                "source_digest": rec.source_digest,
                "status": ToolLifecycleState.ACTIVE.value,
                "created_at": rec.created_at,
                "rehydrated_at": timestamp_now(),
                "manifest": manifest.to_dict(),
                "validation": report.to_dict(),
            }

            registry.register(
                tool=tool_instance,
                version=rec.version,
                provenance=provenance,
                allow_upgrade=True,
            )

            # 8. Mark dynamically authorized
            if hasattr(registry, "authorize_dynamic"):
                registry.authorize_dynamic(rec.name)

            # 9. Register capability if present
            caps_to_register = set()
            if manifest.capability:
                caps_to_register.add(manifest.capability)
            tool_cap = getattr(tool_instance, "capability", None)
            if tool_cap:
                caps_to_register.add(tool_cap)

            for cap_id in caps_to_register:
                try:
                    from core.capabilities import registry as cap_registry, Capability
                    if not cap_registry.get(cap_id):
                        raw_text = f"{manifest.name.replace('_', ' ')} {manifest.description or ''}"
                        keywords = list(set([tok for tok in raw_text.lower().split() if len(tok) > 2]))
                        cap_registry.register(
                            Capability(
                                capability_id=cap_id,
                                name=manifest.name,
                                description=manifest.description,
                                category="custom",
                                discovery_metadata={"tool": manifest.name, "keywords": keywords},
                            )
                        )
                except Exception as cap_err:
                    logger.debug("Capability registration skipped for %s: %s", cap_id, cap_err)

            logger.info("Dynamic tool rehydrated successfully: name=%s version=%d", name, rec.version)
            stats["rehydrated"].append({"name": name, "version": rec.version})

        except Exception as tool_err:
            logger.warning("Unexpected error during rehydration of dynamic tool '%s': %s", name, tool_err)
            stats["rejected"].append({"name": name, "reason": f"UNEXPECTED_ERROR: {tool_err}"})

    logger.info(
        "Dynamic tool rehydration completed: %d active tools restored, %d rejected",
        len(stats["rehydrated"]),
        len(stats["rejected"]),
    )
    return stats

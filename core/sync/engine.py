"""
SyncEngine: Central orchestrator for local event log, outbox, inbox, conflict resolution,
and bidirectional replication.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from core.logger import logger
from core.sync.client import SyncNetworkException, SyncRelayClient, SyncTimeoutException
from core.sync.conflict import ConflictManager
from core.sync.event_log import EventLog
from core.sync.identity import NodeIdentityManager
from core.sync.inbox import InboxProcessor
from core.sync.models import OutboxStatus, SyncEvent, SyncEventType, SyncNode, SyncNodeType, SyncState
from core.sync.outbox import OutboxManager
from core.sync.replication import ExperienceReplicationAdapter, ToolReplicationAdapter
from memory.models import AuraExperienceRecord, SyncCursorRecord, SyncEventRecord, SyncOutboxRecord
from memory.sqlite import SessionLocal, db_lock, init_sync_tables


class SyncEngine:
    def __init__(
        self,
        node_type: str = SyncNodeType.LAPTOP.value,
        session_factory=SessionLocal,
    ):
        self._session_factory = session_factory
        init_sync_tables()
        self.node = NodeIdentityManager.get_local_node(node_type=node_type)
        self.event_log = EventLog(session_factory)
        self.outbox = OutboxManager(session_factory)
        self.conflicts = ConflictManager(session_factory)
        self.inbox = InboxProcessor(
            session_factory=session_factory,
            conflict_manager=self.conflicts,
            dispatch_handler=self._dispatch_domain_event,
        )
        self.state = SyncState.ONLINE

    def _dispatch_domain_event(self, event: SyncEvent):
        """Dispatches an incoming, verified event to domain subsystems."""
        if event.entity_type == "experience":
            ExperienceReplicationAdapter.apply_event(event, self._session_factory)
        elif event.entity_type == "tool":
            ToolReplicationAdapter.apply_event(event, self._session_factory)

    def log_local_experience(self, exp: AuraExperienceRecord, target_node_id: str = "") -> SyncEvent:
        """Turns an operational experience into a synchronized event."""
        event = ExperienceReplicationAdapter.to_event(exp, self.node.node_id)
        return self.event_log.append_event(event, target_node_id=target_node_id, enqueue_outbox=True)

    def log_local_tool_verified(
        self,
        tool_name: str,
        schema: dict,
        evidence: list,
        provenance: dict,
        target_node_id: str = "",
    ) -> SyncEvent:
        """Turns a locally discovered & verified tool into a replicated event."""
        event = ToolReplicationAdapter.to_event(
            tool_name=tool_name,
            schema=schema,
            evidence=evidence,
            provenance_info=provenance,
            origin_node_id=self.node.node_id,
        )
        return self.event_log.append_event(event, target_node_id=target_node_id, enqueue_outbox=True)

    def process_outbox(self, relay_client: SyncRelayClient, ignore_backoff: bool = False) -> int:
        """
        Dispatches pending outbox events to the relay.
        Returns number of successfully acknowledged events.
        """
        pending = self.outbox.get_pending(limit=50, ignore_backoff=ignore_backoff)
        if not pending:
            return 0

        event_ids = [evt.event_id for _, evt in pending]
        self.outbox.mark_sending(event_ids)
        events_to_send = [evt for _, evt in pending]

        try:
            acked_ids, conflict_details = relay_client.push_events(self.node.node_id, events_to_send)
            if acked_ids:
                self.outbox.mark_acknowledged(acked_ids)

            for conf in conflict_details:
                eid = conf.get("event_id")
                if eid:
                    self.outbox.mark_quarantined(eid, conf.get("error", "Remote conflict"))

            return len(acked_ids)
        except SyncTimeoutException as te:
            # Network timeout -> mark retry with backoff, NEVER delete!
            logger.warning(f"Outbox transmission timed out: {te}")
            for eid in event_ids:
                self.outbox.mark_retry(eid, error_message=str(te))
            self.state = SyncState.DEGRADED
            return 0
        except SyncNetworkException as ne:
            logger.warning(f"Outbox network failure: {ne}")
            for eid in event_ids:
                self.outbox.mark_retry(eid, error_message=str(ne))
            self.state = SyncState.OFFLINE
            return 0
        except Exception as e:
            logger.error(f"Unexpected error in outbox push: {e}")
            for eid in event_ids:
                self.outbox.mark_retry(eid, error_message=str(e))
            self.state = SyncState.ERROR
            return 0

    def process_inbox_from_relay(self, relay_client: SyncRelayClient, peer_node_id: str = "") -> int:
        """
        Pulls incoming events from relay and processes them into local state.
        Returns number of newly received events.
        """
        # Read local cursor for peer
        cursor_seq = 0
        with db_lock:
            session = self._session_factory()
            try:
                rec = (
                    session.query(SyncCursorRecord)
                    .filter_by(node_id=self.node.node_id, peer_node_id=peer_node_id)
                    .first()
                )
                if rec:
                    cursor_seq = rec.last_sequence
            finally:
                session.close()

        try:
            events, new_cursor, has_more = relay_client.pull_events(
                node_id=self.node.node_id,
                after_sequence=cursor_seq,
                limit=100,
            )
            if not events:
                return 0

            acked_ids, conflicts = self.inbox.process_incoming_batch(
                events=events,
                receiver_node_id=self.node.node_id,
            )

            # Send ACK back to relay
            if acked_ids:
                relay_client.ack_events(self.node.node_id, acked_ids)

            return len(acked_ids)
        except SyncTimeoutException as te:
            logger.warning(f"Pull from relay timed out: {te}")
            self.state = SyncState.DEGRADED
            return 0
        except SyncNetworkException as ne:
            logger.warning(f"Pull from relay network error: {ne}")
            self.state = SyncState.OFFLINE
            return 0
        except Exception as e:
            logger.error(f"Error pulling from relay: {e}")
            self.state = SyncState.ERROR
            return 0

    def sync_cycle(self, relay_client: SyncRelayClient, peer_node_id: str = "") -> dict[str, int]:
        """Runs a complete bidirectional sync cycle."""
        self.state = SyncState.SYNCING
        sent = self.process_outbox(relay_client)
        received = self.process_inbox_from_relay(relay_client, peer_node_id=peer_node_id)
        self.state = SyncState.SYNCED if self.outbox.pending_count() == 0 else SyncState.ONLINE
        return {"sent": sent, "received": received}

    def get_diagnostics(self) -> dict[str, Any]:
        """Returns structured diagnostic status for observability."""
        with db_lock:
            session = self._session_factory()
            try:
                pending_count = (
                    session.query(SyncOutboxRecord)
                    .filter_by(status=OutboxStatus.PENDING.value)
                    .count()
                )
                acked_count = (
                    session.query(SyncOutboxRecord)
                    .filter_by(status=OutboxStatus.ACKNOWLEDGED.value)
                    .count()
                )
                total_events = session.query(SyncEventRecord).count()
                quarantined_conflicts = len(self.conflicts.list_conflicts("QUARANTINED"))
                cursors = [
                    {"peer": c.peer_node_id, "last_sequence": c.last_sequence, "updated_at": c.updated_at}
                    for c in session.query(SyncCursorRecord).filter_by(node_id=self.node.node_id).all()
                ]

                return {
                    "node_id": self.node.node_id,
                    "node_type": self.node.node_type,
                    "sync_state": self.state.value if isinstance(self.state, SyncState) else str(self.state),
                    "pending_outbox_events": pending_count,
                    "acknowledged_outbox_events": acked_count,
                    "total_local_events": total_events,
                    "quarantined_conflicts": quarantined_conflicts,
                    "cursors": cursors,
                    "last_seen": datetime.now().isoformat(timespec="seconds"),
                }
            finally:
                session.close()

"""Cross-workflow suppression from network connection-request state."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from apps.network_automation.models import (
    AcceptanceStatus,
    CandidateStatus,
)
from apps.network_automation.store import Store as NetworkStore
from packages.linkedin_common import (
    canonical_linkedin_profile_identity,
    linkedin_profile_identity_keys,
)

from .models import Lead, MessageStatus, OutreachState
from .utils import now_iso, truncate_evidence

NETWORK_STATE_DIR_ENV = "LINKEDIN_TOOLS_NETWORK_STATE_DIR"

NETWORK_CONNECTION_SUPPRESSION_STATUSES = {
    CandidateStatus.PENDING_PROVISIONAL,
    CandidateStatus.PENDING,
    CandidateStatus.ACCEPTED,
    CandidateStatus.ALREADY_PENDING,
    CandidateStatus.AUDIT_TOP_UP,
}

NETWORK_ACCEPTANCE_SUPPRESSION_STATUSES = {
    AcceptanceStatus.SENT,
    AcceptanceStatus.PENDING,
    AcceptanceStatus.ACCEPTED,
}

OUTREACH_STATUSES_TO_PRESERVE = {
    MessageStatus.SENT,
    MessageStatus.MANUALLY_SENT,
    MessageStatus.REPLIED,
    MessageStatus.REPLIED_NOT_FIT,
    MessageStatus.REPLIED_FUTURE,
    MessageStatus.REPLIED_UNKNOWN,
    MessageStatus.SUPPRESSED,
}


@dataclass(frozen=True, slots=True)
class NetworkSuppressionHit:
    identity: str
    reason: str
    name: str
    source: str
    status: str


def load_network_connection_suppression_index(
    state_dir: str | Path | None = None,
) -> dict[str, NetworkSuppressionHit]:
    store = NetworkStore(_network_state_dir(state_dir))
    index: dict[str, NetworkSuppressionHit] = {}
    _index_active_run(store, index)
    _index_acceptance_ledger(store, index)
    return index


def apply_network_suppression_to_outreach_state(
    state: OutreachState,
    *,
    state_dir: str | Path | None = None,
) -> int:
    index = load_network_connection_suppression_index(state_dir)
    suppressed = 0
    for lead in state.leads:
        hit = network_suppression_hit_for_lead(lead, index)
        if hit is None or lead.message_status in OUTREACH_STATUSES_TO_PRESERVE:
            continue
        apply_network_suppression_to_lead(lead, hit)
        suppressed += 1
    return suppressed


def network_suppression_hit_for_lead(
    lead: Lead,
    index: dict[str, NetworkSuppressionHit] | None = None,
) -> NetworkSuppressionHit | None:
    suppression_index = index if index is not None else load_network_connection_suppression_index()
    for key in linkedin_profile_identity_keys(lead.profile_url, lead.sales_profile_urn):
        hit = suppression_index.get(key)
        if hit is not None:
            return hit
    return None


def apply_network_suppression_to_lead(lead: Lead, hit: NetworkSuppressionHit) -> None:
    at = now_iso()
    lead.message_status = MessageStatus.SUPPRESSED
    lead.message_status_at = at
    lead.updated_at = at
    note = (
        "cross-workflow suppression: network connection already recorded "
        f"for {hit.name} ({hit.status}); source={hit.source}; identity={hit.identity}"
    )
    if note not in lead.notes:
        lead.notes.append(truncate_evidence(note, 500))


def _network_state_dir(state_dir: str | Path | None) -> Path | None:
    if state_dir is not None:
        return Path(state_dir)
    configured = os.environ.get(NETWORK_STATE_DIR_ENV)
    return Path(configured) if configured else None


def _index_active_run(
    store: NetworkStore,
    index: dict[str, NetworkSuppressionHit],
) -> None:
    try:
        run = store.load_run()
    except OSError:
        return
    for event in run.candidates:
        if event.status not in NETWORK_CONNECTION_SUPPRESSION_STATUSES:
            continue
        identity = canonical_linkedin_profile_identity(event.profile_url)
        if identity is None:
            continue
        index.setdefault(
            identity,
            NetworkSuppressionHit(
                identity=identity,
                reason="network-candidate",
                name=event.name,
                source=event.source,
                status=event.status.value,
            ),
        )


def _index_acceptance_ledger(
    store: NetworkStore,
    index: dict[str, NetworkSuppressionHit],
) -> None:
    ledger = store.load_acceptance_ledger()
    for invitation in ledger.invitations:
        if invitation.latest_status not in NETWORK_ACCEPTANCE_SUPPRESSION_STATUSES:
            continue
        identity = canonical_linkedin_profile_identity(invitation.profile_url)
        if identity is None:
            continue
        index.setdefault(
            identity,
            NetworkSuppressionHit(
                identity=identity,
                reason="acceptance-ledger",
                name=invitation.name,
                source=invitation.source,
                status=invitation.latest_status.value,
            ),
        )

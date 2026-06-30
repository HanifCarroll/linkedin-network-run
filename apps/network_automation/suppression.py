"""Cross-workflow suppression from recruiter/agency/advisor outreach state."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from apps.recruiter_agency_outreach.models import MessageStatus
from apps.recruiter_agency_outreach.storage import Store as OutreachStore
from packages.linkedin_common import (
    canonical_linkedin_profile_identity,
    linkedin_profile_identity_keys,
)

from .models import CandidateEvent, CandidateObservation, CandidateStatus, Run, now_utc

OUTREACH_STATE_DIR_ENV = "LINKEDIN_TOOLS_RECRUITER_AGENCY_STATE_DIR"

OUTREACH_MESSAGE_SUPPRESSION_STATUSES = {
    MessageStatus.SENT,
    MessageStatus.MANUALLY_SENT,
    MessageStatus.REPLIED,
    MessageStatus.REPLIED_NOT_FIT,
    MessageStatus.REPLIED_FUTURE,
    MessageStatus.REPLIED_UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class OutreachSuppressionHit:
    identity: str
    reason: str
    lead_id: str
    name: str
    source: str
    status: str


def load_outreach_message_suppression_index(
    state_dir: str | Path | None = None,
) -> dict[str, OutreachSuppressionHit]:
    store = OutreachStore(_outreach_state_dir(state_dir))
    try:
        state = store.load()
    except OSError:
        return {}
    index: dict[str, OutreachSuppressionHit] = {}
    for lead in state.leads:
        if lead.message_status not in OUTREACH_MESSAGE_SUPPRESSION_STATUSES:
            continue
        for identity in linkedin_profile_identity_keys(lead.profile_url, lead.sales_profile_urn):
            index.setdefault(
                identity,
                OutreachSuppressionHit(
                    identity=identity,
                    reason="outreach-message",
                    lead_id=lead.id,
                    name=lead.name,
                    source=lead.source,
                    status=lead.message_status.value,
                ),
            )
    return index


def skip_outreach_suppressed_observations(
    run: Run,
    index: dict[str, OutreachSuppressionHit] | None = None,
) -> list[CandidateEvent]:
    suppression_index = index if index is not None else load_outreach_message_suppression_index()
    events: list[CandidateEvent] = []
    for observation in run.observations:
        if observation.menu_state != "connectable":
            continue
        if run.has_candidate_event_for_observation(observation):
            continue
        hit = outreach_suppression_hit_for_observation(observation, suppression_index)
        if hit is None:
            continue
        event = CandidateEvent(
            at=now_utc(),
            source=observation.source,
            name=observation.name,
            profile_url=(
                canonical_linkedin_profile_identity(
                    observation.profile_url,
                    observation.sales_profile_urn,
                )
                or observation.profile_url
            ),
            status=CandidateStatus.SKIPPED,
            note=(
                "cross-workflow suppression: outreach message already sent "
                f"to {hit.name} ({hit.status}); lead_id={hit.lead_id}; identity={hit.identity}"
            ),
        )
        run.candidates.append(event)
        events.append(event)
    if events:
        run.mark_updated()
    return events


def outreach_suppression_hit_for_observation(
    observation: CandidateObservation,
    index: dict[str, OutreachSuppressionHit] | None = None,
) -> OutreachSuppressionHit | None:
    suppression_index = index if index is not None else load_outreach_message_suppression_index()
    for key in linkedin_profile_identity_keys(
        observation.profile_url,
        observation.sales_profile_urn,
    ):
        hit = suppression_index.get(key)
        if hit is not None:
            return hit
    return None


def _outreach_state_dir(state_dir: str | Path | None) -> Path | None:
    if state_dir is not None:
        return Path(state_dir)
    configured = os.environ.get(OUTREACH_STATE_DIR_ENV)
    return Path(configured) if configured else None

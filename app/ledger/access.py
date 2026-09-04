from __future__ import annotations

from .queries import Ledger


def rejudge_private(ledger: Ledger, event_id: str, extra_viewers: list[str] | None = None) -> list[str]:
    """Build the known_by list when changing a public event to private.

    The list is: original participants + anyone who referenced this event
    while it was public. This is intentionally approximate; a director can
    confirm/edit the final list.
    """
    event = ledger.by_id.get(event_id)
    if event is None or event["kind"] != "narrative":
        return []

    viewers = set(event.get("participants", []))
    viewers.update(extra_viewers or [])

    # Any later narrative that mentions the event body is treated as a referrer.
    subject_words = set(event.get("participants", []))
    for other in ledger.narratives:
        if other["id"] == event_id:
            continue
        body = other.get("body", "")
        if any(word in body for word in subject_words):
            viewers.update(other.get("participants", []))
    return sorted(viewers)


def rejudge_public(ledger: Ledger, event_id: str) -> None:
    """Change a private event back to public by clearing its access override."""
    if event_id in ledger.by_id:
        ledger.save.access_overrides[event_id] = None


def apply_access_override(ledger: Ledger, event_id: str, known_by: list[str] | None) -> None:
    ledger.save.access_overrides[event_id] = known_by
    ledger.persist_save()
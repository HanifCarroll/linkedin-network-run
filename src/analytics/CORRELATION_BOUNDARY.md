# Analytics export correlation boundary

LinkedIn does not expose a download request identifier or file identifier for creator-content
exports. The receipt therefore records a bounded temporal association: the requested account and
seven-day period, exact result page, durable confirmation attempt, pre-confirm snapshot, operation
window, and complete candidate set immediately before publication.

This evidence is not proof of causality. Two concurrent exports for the same account and same
period can produce indistinguishable files inside the same operation window. Without a LinkedIn
identifier, the subsystem cannot tell which action produced such a file. Callers must prevent that
kind of concurrent export; if it happens despite that boundary, the ambiguity is unavoidable.

Every distinguishable ambiguity fails closed and becomes sticky `needs_reconciliation` state. That
includes multiple candidates, a different account or period, an out-of-window file, a changed
candidate identity, and a contaminating candidate that is later deleted. A retry cannot erase that
evidence or click Confirm again.

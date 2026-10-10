"""Keep a partial attempt distinct from the last complete Canvas collection."""
from datetime import datetime, timezone


def summary(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    complete = snapshot.get("last_complete_at") or (snapshot.get("collected_at") if snapshot.get("complete") else None)
    age = None
    try:
        stamp = datetime.fromisoformat(complete.replace("Z", "+00:00"))
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        age = max(0, int((now-stamp).total_seconds()))
    except (AttributeError, ValueError, TypeError): complete = None
    state = "missing" if not snapshot.get("collected_at") else "partial" if not snapshot.get("complete") else "stale" if age is None or age >= 86400 else "current"
    return {"state": state, "lastCompleteAt": complete, "ageSeconds": age,
            "warnings": snapshot.get("warnings") or [], "scope": "课程名单、Canvas 作业与公告"}

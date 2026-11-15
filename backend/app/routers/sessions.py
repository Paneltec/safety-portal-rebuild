"""Active session administration and per-user session history (admin only)."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Caller, require_admin
from ..models import User, UserSession, utcnow
from ..services import audit, session_out

router = APIRouter(prefix="/api/admin", tags=["sessions"])


class BulkRevokeIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=1000)


@router.get("/active-sessions")
def list_active_sessions(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    now = utcnow()
    rows = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.org_id == caller.org_id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)
        .order_by(UserSession.last_seen_at.desc(), UserSession.id)
        .limit(limit)
        .offset(offset)
    ).all()
    sessions = [session_out(s, current_id=caller.session.id, user=u) for s, u in rows]
    return {"sessions": sessions, "count": len(sessions)}


@router.post("/active-sessions/bulk-revoke")
def bulk_revoke(
    body: BulkRevokeIn, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """Revoke many sessions. The caller's own current session is always skipped."""
    revoked = 0
    skipped_self = False
    not_found: list[str] = []
    for jti in dict.fromkeys(body.ids):
        session = db.get(UserSession, jti)
        if session is None or session.org_id != caller.org_id or session.revoked_at is not None:
            not_found.append(jti)
            continue
        if session.id == caller.session.id:
            skipped_self = True
            continue
        session.revoked_at = utcnow()
        session.revoke_reason = "revoked_by_admin"
        revoked += 1
    if revoked:
        audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="session.bulk_revoked",
              target_type="session", target_id=None, detail={"revoked": revoked})
    db.commit()
    return {"requested": len(body.ids), "revoked": revoked, "skipped_self": skipped_self, "not_found": not_found}


@router.delete("/active-sessions/{jti}", status_code=204)
def revoke_session(
    jti: str, request: Request, caller: Caller = Depends(require_admin), db: Session = Depends(get_db)
) -> Response:
    session = db.get(UserSession, jti)
    if session is None or session.org_id != caller.org_id or session.revoked_at is not None:
        raise HTTPException(404, "Session not found")
    if session.id == caller.session.id:
        raise HTTPException(
            400, "You can't revoke your own current session. Use 'Force logout everyone' or sign out normally."
        )
    session.revoked_at = utcnow()
    session.revoke_reason = "revoked_by_admin"
    audit(db, request, org_id=caller.org_id, actor_id=caller.user.id, action="session.revoked",
          target_type="session", target_id=session.id, detail={"user_id": session.user_id})
    db.commit()
    return Response(status_code=204)


@router.get("/users/{user_id}/session-history")
def session_history(
    user_id: str,
    limit: int = Query(50, ge=1, le=500),
    caller: Caller = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None or user.org_id != caller.org_id:
        raise HTTPException(404, "User not found")
    rows = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.created_at.desc(), UserSession.id)
        .limit(limit)
    ).all()
    history = [session_out(s, current_id=caller.session.id) for s in rows]
    return {"user": {"id": user.id, "name": user.name, "email": user.email}, "count": len(history), "history": history}

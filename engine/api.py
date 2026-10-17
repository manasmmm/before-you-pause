"""FastAPI co-pilot service (skeleton). Storage and partner calls sit behind `repo` and `partner`."""
from __future__ import annotations
from typing import Literal, Optional
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import engine as E

app = FastAPI(title="FinLit Before You Pause", version="1.0.0")
Action = Literal["pause", "reduce", "stop"]
Reason = Literal["need_cash", "market_worry", "better_option", "not_sure"]
Choice = Literal["keep", "reduce", "pause", "stop"]


def service_auth(authorization: str = Header(...)):
    # Next.js BFF calls with a signed service token that carries the verified user id.
    user_id = verify_service_token(authorization)          # raises 401 on failure
    return user_id


class EvaluateIn(BaseModel):
    sip_id: str
    action: Action


class EvaluateOut(BaseModel):
    session_id: str
    eligible: bool
    variant: Literal["treatment", "holdout", "ineligible"]
    skip_reasons: list[str] = []
    template_text: Optional[dict] = None       # deterministic text, renders immediately
    payload: Optional[dict] = None             # safe subset shown in the UI
    caption_url: Optional[str] = None          # poll target for the AI caption


@app.post("/v1/copilot/evaluate", response_model=EvaluateOut)
def evaluate(body: EvaluateIn, user_id: str = Depends(service_auth)):
    if not repo.flag("copilot_enabled"):
        return EvaluateOut(session_id=repo.log_ineligible(user_id, body), eligible=False,
                           variant="ineligible", skip_reasons=["kill_switch"])
    sip = repo.get_sip(user_id, body.sip_id)
    checks = {
        "no_planned_pause": sip.paused_until is None,
        "cooldown_14d_clear": not repo.session_within(body.sip_id, days=14),
        "consent_personalised": repo.consent(user_id, "personalised_insights"),
        "min_history_2_instalments": sip.completed_instalments >= 2,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        return EvaluateOut(session_id=repo.log_ineligible(user_id, body, failed), eligible=False,
                           variant="ineligible", skip_reasons=failed)
    arm = repo.arm(user_id, "copilot_v1")                  # deterministic hash assignment
    payload = repo.payload(body.sip_id)                    # cached nightly build (< 300 ms)
    if payload is None:                                    # stale or failed NAV job: no payload is written
        return EvaluateOut(session_id=repo.log_ineligible(user_id, body, ["no_fresh_payload"]), eligible=False,
                           variant="ineligible", skip_reasons=["no_fresh_payload"])
    sid = repo.open_session(user_id, body, payload, arm, checks)
    if arm == "holdout":                                   # logged, but existing flow runs unchanged
        return EvaluateOut(session_id=sid, eligible=True, variant="holdout")
    queue.enqueue_caption(sid)                             # async LLM caption
    return EvaluateOut(session_id=sid, eligible=True, variant="treatment",
                       template_text=render_templates(payload), payload=payload["ui"],
                       caption_url=f"/v1/copilot/captions/{sid}")


class SimulateIn(BaseModel):
    session_id: str
    mode: Action
    months: int = Field(3, ge=1, le=6)
    new_amount: float = Field(0, ge=0)
    annual_return_pct: Literal[8, 10, 12] = 10


@app.post("/v1/copilot/simulate")
def simulate(body: SimulateIn, user_id: str = Depends(service_auth)):
    p = repo.session_payload(user_id, body.session_id)["engine"]
    n = p["months_left"] if body.mode == "stop" else body.months
    x = body.new_amount if body.mode == "reduce" else 0.0
    if body.mode == "reduce" and x >= p["sip_amount"]:
        raise HTTPException(422, "new_amount must be below the current SIP amount")
    a = body.annual_return_pct / 100
    per = p["sip_amount"] - x
    base = E.months_to_goal(p["corpus_linked"], p["other_sip"], p["sip_amount"], a, p["goal_target"])
    alt = E.months_to_goal(p["corpus_linked"], p["other_sip"], p["sip_amount"], a, p["goal_target"], n, x)
    return {"skipped_inr": per * n,
            "skipped_grown_inr": round(E.skipped_fv(per, n, a, p["months_left"])),
            "corpus_continue_inr": round(E.project_corpus(p["corpus_linked"], p["other_sip"], p["sip_amount"], a,
                p["months_left"])),
            "corpus_change_inr": round(E.project_corpus(p["corpus_linked"], p["other_sip"], p["sip_amount"], a,
                p["months_left"], n, x)),
            "goal_month_shift": alt - base, "disclosures": ["D4"]}


class CaptionOut(BaseModel):
    status: Literal["pending", "ready", "fallback"]
    caption: Optional[dict] = None
    disclosures: list[str] = ["D2", "D3"]


@app.get("/v1/copilot/captions/{session_id}", response_model=CaptionOut)
def caption(session_id: str, user_id: str = Depends(service_auth)):
    row = repo.caption(user_id, session_id)                # accepted only if validated within 1.5 s
    if row is None:
        return CaptionOut(status="pending")
    return CaptionOut(status="ready" if row.served == "llm" else "fallback", caption=row.caption)


class DecisionIn(BaseModel):
    session_id: str
    choice: Choice
    new_amount: Optional[float] = None
    pause_months: Optional[int] = Field(None, ge=1, le=12)
    confirmed: bool


@app.post("/v1/copilot/decision")
def decision(body: DecisionIn, user_id: str = Depends(service_auth),
            idempotency_key: str = Header(..., alias="Idempotency-Key")):
    if not body.confirmed:                                  # nothing changes without an explicit confirm tap
        raise HTTPException(409, "confirmation required")
    if body.choice == "reduce" and body.new_amount is None:
        raise HTTPException(422, "new_amount required")
    if body.choice == "pause" and body.pause_months is None:
        raise HTTPException(422, "pause_months required")
    d = repo.record_decision(user_id, body, idempotency_key)    # audit-logged with the exact payload shown
    if body.choice != "keep":
        partner.apply_sip_change(user_id, d)                # user-instructed action, executed by the licensed entity
    return {"decision_id": d.id, "checkin_due_on": repo.schedule_checkin(d.id, days=90)}

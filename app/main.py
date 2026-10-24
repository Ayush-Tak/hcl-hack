"""FastAPI application for the Daily Banking Assistant."""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import auth, db, engine, guard, seed
from app.config import settings
from app.trace import Trace


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Demo accounts have well-known PINs (1234, officer 9999), so they're only created in demo
    # mode, and only into an empty database. Anywhere else we just create the tables.
    if settings.demo_mode:
        seed.seed()
    else:
        db.init_db()
    yield


app = FastAPI(title="Daily Banking Assistant API", version="1.0.0", lifespan=lifespan)

# CORS configuration for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request schemas
class LoginRequest(BaseModel):
    customer_id: str
    pin: str


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ActionConfirmRequest(BaseModel):
    otp: str


class SimTransactionRequest(BaseModel):
    amount: int
    merchant: str
    channel: str = "UPI"
    city: str | None = None


# Routes
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login")
def login_route(req: LoginRequest) -> dict[str, Any]:
    result = auth.login(req.customer_id, req.pin)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid customer ID or PIN")
    return result


@app.post("/chat")
def chat_route(
    req: ChatRequest, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    # 1. Guard check
    guard_res = guard.check(req.message)
    if guard_res["blocked"]:
        tr = Trace(user["customer_id"], req.message)
        tr.add("guard", "input_guard", ok=False, reason=guard_res["reason"])
        tr.finish(answer=guard_res["reason"], answer_type="refused", mode="guard")
        return {
            "trace_id": tr.id,
            "answer": f"Request refused: {guard_res['reason']}",
            "answer_type": "refused",
            "data": None,
            "citations": [],
            "pending_action": None,
            "warnings": [
                {"source": "guard", "level": "high", "message": guard_res["reason"]}
            ],
            "tools_invoked": [],
            "trace_url": f"/ops/traces/{tr.id}",
            "latency_ms": tr.elapsed_ms(),
        }

    # 2. Call engine with cleaned message (sync function runs in threadpool)
    return engine.handle_chat(
        customer_id=user["customer_id"],
        message=guard_res["clean_message"],
        session_id=req.session_id,
    ).model_dump()


@app.post("/actions/{action_id}/confirm")
def confirm_action_route(
    action_id: str,
    req: ActionConfirmRequest,
    user: dict = Depends(auth.current_user),
) -> dict[str, Any]:
    return engine.confirm_action(
        customer_id=user["customer_id"],
        action_id=action_id,
        otp=req.otp,
    ).model_dump()


@app.get("/trace/{trace_id}")
@app.get("/ops/traces/{trace_id}")
def trace_route(
    trace_id: str, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    trace = engine.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    # Verify authorization: officer, or trace belongs to customer
    if user.get("role") != "officer" and trace.get("customer_id") != user.get(
        "customer_id"
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return trace


@app.get("/me/alerts")
def my_alerts_route(user: dict = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return engine.list_alerts(customer_id=user["customer_id"])


@app.get("/ops/review-queue")
def review_queue_route(user: dict = Depends(auth.current_user)) -> list[dict[str, Any]]:
    auth.require_officer(user)
    return engine.list_alerts(customer_id=None)


@app.get("/ops/metrics")
def metrics_route(user: dict = Depends(auth.current_user)) -> dict[str, Any]:
    auth.require_officer(user)
    return engine.metrics()


@app.post("/sim/transactions")
def sim_transactions_route(
    req: SimTransactionRequest, user: dict = Depends(auth.current_user)
) -> dict[str, Any]:
    return engine.ingest_transaction(
        customer_id=user["customer_id"],
        txn=req.model_dump(),
    )

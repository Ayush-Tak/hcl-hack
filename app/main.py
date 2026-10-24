"""Minimal FastAPI entrypoint for the demo chat service."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import auth, engine, seed
from app.models import ChatResponse


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed.seed()
    yield


app = FastAPI(title="Daily Banking Assistant", lifespan=lifespan)


class LoginRequest(BaseModel):
    customer_id: str
    pin: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default_factory=lambda: str(uuid4()))


class ConfirmRequest(BaseModel):
    otp: str = Field(pattern=r"^\d{6}$")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/auth/login")
def login(request: LoginRequest) -> dict:
    result = auth.login(request.customer_id, request.pin)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid customer ID or PIN")
    return result


@app.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest, user: dict = Depends(auth.current_user)
) -> ChatResponse:
    return engine.handle_chat(
        user["customer_id"], request.message, request.session_id
    )


@app.post("/actions/{action_id}/confirm", response_model=ChatResponse)
def confirm_action(
    action_id: str,
    request: ConfirmRequest,
    user: dict = Depends(auth.current_user),
) -> ChatResponse:
    return engine.confirm_action(user["customer_id"], action_id, request.otp)

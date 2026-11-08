"""Authenticated FastAPI endpoints for reviewing and acting on draft orders."""

from __future__ import annotations

import hmac
import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Annotated

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field, field_validator
from .gmail_client import GmailAPIError, GmailAuthenticationError, GmailClient

from .orders import (
    InsufficientStockError,
    InvalidOrderStateError,
    Order,
    OrderAuthorizationError,
    OrderNotFoundError,
    OrderRepository,
    OrderRepositoryError,
    OrderStatus,
    ReviewCase,
)

load_dotenv()

_JWT_ISSUER = "gmail-order-processor-dashboard"
_JWT_ALGORITHM = "HS256"
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip()
        if parseaddr(value)[1] != value or value.count("@") != 1 or any(
            character.isspace() for character in value
        ):
            raise ValueError("Enter a valid email address")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class OwnerIdentity(BaseModel):
    email: str
    role: str = "owner"


class SendReviewReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


def create_app(
    order_repository: OrderRepository | None = None,
    *,
    owner_email: str | None = None,
    owner_password: str | None = None,
    jwt_secret: str | None = None,
    token_minutes: int | None = None,
    cors_origins: list[str] | None = None,
    gmail_client: GmailClient | None = None,
) -> FastAPI:
    """Build the dashboard API; injectable settings keep auth testable."""
    repository = order_repository or OrderRepository()
    mailer = gmail_client or GmailClient(
        os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json"),
        os.getenv("GMAIL_TOKEN_FILE", "token.json"),
    )
    configured_email = (owner_email or os.getenv("DASHBOARD_OWNER_EMAIL", "")).strip()
    configured_password = owner_password or os.getenv("DASHBOARD_OWNER_PASSWORD", "")
    configured_secret = jwt_secret or os.getenv("DASHBOARD_SECRET_KEY", "")
    configured_minutes = token_minutes or int(os.getenv("DASHBOARD_TOKEN_MINUTES", "60"))
    configured_origins = cors_origins or [
        origin.strip()
        for origin in os.getenv(
            "DASHBOARD_CORS_ORIGINS", "http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ]

    app = FastAPI(
        title="Gmail Order Processor Dashboard API",
        version="1.0.0",
        description="Authenticated owner review and approval endpoints for draft orders.",
    )
    app.state.order_repository = repository
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def require_auth_configuration() -> tuple[str, str, str]:
        if (
            not configured_email
            or len(configured_password) < 12
            or len(configured_secret.encode("utf-8")) < 32
            or configured_minutes < 1
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Dashboard auth is not configured. Set the owner email, a password "
                    "of at least 12 characters, and a secret key of at least 32 bytes."
                ),
            )
        return configured_email, configured_password, configured_secret

    async def require_owner(
        token: Annotated[str, Depends(_oauth2_scheme)],
    ) -> OwnerIdentity:
        owner, _password, secret = require_auth_configuration()
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=[_JWT_ALGORITHM],
                issuer=_JWT_ISSUER,
                options={"require": ["exp", "iat", "iss", "sub", "role"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Login expired; please sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token.",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

        if claims.get("role") != "owner" or claims.get("sub") != owner.casefold():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Owner authorization is required.",
            )
        return OwnerIdentity(email=owner, role="owner")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/login", response_model=LoginResponse)
    async def login(credentials: LoginRequest) -> LoginResponse:
        owner, password, secret = require_auth_configuration()
        email_matches = hmac.compare_digest(
            credentials.email.strip().casefold().encode("utf-8"),
            owner.casefold().encode("utf-8"),
        )
        password_matches = hmac.compare_digest(
            credentials.password.encode("utf-8"), password.encode("utf-8")
        )
        if not (email_matches and password_matches):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email or password is incorrect.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=configured_minutes)
        access_token = jwt.encode(
            {
                "sub": owner.casefold(),
                "role": "owner",
                "iss": _JWT_ISSUER,
                "iat": now,
                "exp": expires_at,
            },
            secret,
            algorithm=_JWT_ALGORITHM,
        )
        return LoginResponse(
            access_token=access_token,
            expires_in=configured_minutes * 60,
        )

    @app.get("/api/me", response_model=OwnerIdentity)
    async def me(owner: OwnerIdentity = Depends(require_owner)) -> OwnerIdentity:
        return owner

    @app.get("/api/orders", response_model=list[Order])
    async def list_orders(
        owner: OwnerIdentity = Depends(require_owner),
        order_status: Annotated[
            OrderStatus | None, Query(alias="status")
        ] = None,
    ) -> list[Order]:
        del owner
        try:
            return repository.list_orders(order_status)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not load orders.",
            ) from exc

    @app.get("/api/review-cases", response_model=list[ReviewCase])
    async def list_review_cases(owner: OwnerIdentity = Depends(require_owner)) -> list[ReviewCase]:
        del owner
        try:
            return repository.list_review_cases()
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Could not load review cases.") from exc

    @app.post("/api/review-cases/{message_id}/send")
    async def send_review_reply(
        message_id: str,
        request: SendReviewReplyRequest,
        owner: OwnerIdentity = Depends(require_owner),
    ) -> dict[str, str]:
        del owner
        case = repository.get_review_case(message_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Review case not found.")
        if case.sent_at:
            raise HTTPException(status_code=409, detail="A reply was already sent for this case.")
        recipient = parseaddr(case.sender)[1].strip()
        if not recipient or "@" not in recipient:
            raise HTTPException(status_code=400, detail="The email sender has no valid reply address.")
        body = request.body.strip()
        if not body:
            raise HTTPException(status_code=422, detail="Reply text cannot be empty.")
        subject = case.reply_subject or case.subject
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        try:
            sent_id = mailer.send_message(
                recipient, subject, body, thread_id=case.thread_id,
                in_reply_to=case.original_rfc_message_id,
            )
            repository.mark_review_case_sent(message_id, body)
            return {"status": "sent", "recipient": recipient, "gmail_message_id": sent_id}
        except GmailAuthenticationError as exc:
            print(f"Gmail auth error: {exc}")
            raise HTTPException(status_code=503, detail=f"Gmail authorization required: {exc}") from exc
        except GmailAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Could not record sent reply status.") from exc

    @app.get("/api/orders/{order_id}", response_model=Order)
    async def get_order(
        order_id: int, owner: OwnerIdentity = Depends(require_owner)
    ) -> Order:
        del owner
        order = repository.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found.")
        return order

    @app.post("/api/orders/{order_id}/approve", response_model=Order)
    async def approve_order(
        order_id: int, owner: OwnerIdentity = Depends(require_owner)
    ) -> Order:
        del owner
        try:
            draft = repository.get_order(order_id)
            if draft is None:
                raise OrderNotFoundError(f"Order not found: {order_id}")
            if draft.status is OrderStatus.DRAFT and not draft.response_body:
                raise InvalidOrderStateError(
                    "This order cannot be approved until its reply draft is saved."
                )
            return repository.confirm_order(order_id, authorized=True)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Order not found.") from exc
        except InsufficientStockError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "There is not enough stock to approve this order.",
                    "sku": exc.sku,
                    "requested": exc.requested,
                    "available": exc.available,
                },
            ) from exc
        except (InvalidOrderStateError, OrderAuthorizationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OrderRepositoryError as exc:
            raise HTTPException(status_code=400, detail="Could not approve order.") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Could not approve order.") from exc

    @app.post("/api/orders/{order_id}/reject", response_model=Order)
    async def reject_order(
        order_id: int, owner: OwnerIdentity = Depends(require_owner)
    ) -> Order:
        del owner
        order = repository.get_order(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order.status is not OrderStatus.DRAFT:
            raise HTTPException(
                status_code=409,
                detail="Only draft orders can be rejected from the dashboard.",
            )
        try:
            return repository.cancel_order(order_id, authorized=True)
        except (OrderNotFoundError, InvalidOrderStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except OrderRepositoryError as exc:
            raise HTTPException(status_code=400, detail="Could not reject order.") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail="Could not reject order.") from exc

    return app


app = create_app()

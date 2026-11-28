"""Authenticated analyst API for a synthetic fraud case-review workflow."""
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
from secrets import compare_digest
from secrets import token_urlsafe
from threading import Event, Thread
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.api.case_store import CaseStore, Conflict
from src.api.ring_webhook import AlertWebhookConfig, RingAlertDispatcher


Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.:-]{1,96}$")]
Probability = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
PaymentAmount = Annotated[float, Field(strict=True, ge=0, le=1e12, allow_inf_nan=False)]
EventTimestamp = Annotated[float, Field(strict=True, ge=0, le=253402300799, allow_inf_nan=False)]
CaseState = Literal["open", "in_review", "escalated", "dismissed"]
GraphNodeType = Literal[
    "event", "customer_id", "merchant_id", "device_id", "ip_id", "address_id",
    "promo_id", "bank_account_id",
]


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: Identifier
    customer_id: Identifier
    merchant_id: Identifier
    device_id: Identifier | None = None
    ip_id: Identifier | None = None
    address_id: Identifier | None = None
    promo_id: Identifier | None = None
    bank_account_id: Identifier | None = None
    amount: PaymentAmount
    timestamp: EventTimestamp
    rto_flag: bool = False
    chargeback_flag: bool = False


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: Identifier
    transactions: list[Transaction] = Field(min_length=1, max_length=500)
    risk_scores: dict[Identifier, Probability] = Field(default_factory=dict, max_length=500)

    @model_validator(mode="after")
    def validate_batch(self):
        event_ids = {event.transaction_id for event in self.transactions}
        if len(event_ids) != len(self.transactions):
            raise ValueError("Transaction IDs must be unique within a batch.")
        customers = {event.customer_id for event in self.transactions}
        if not set(self.risk_scores) <= customers:
            raise ValueError("Scores must correspond to customers in the batch.")
        return self


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=0)
    state: CaseState
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]


PROJECTION_PAGE_SIZE = 12
MAX_PROJECTION_PAGES = 8
logger = logging.getLogger(__name__)


class ProjectionUnavailable(RuntimeError):
    """The graph cannot serve a complete current view."""


def graph_cursor(graph_backend, workspace_id: str) -> int:
    cursor = graph_backend.get_cursor(workspace_id)
    if type(cursor) is not int or cursor < 0:
        raise ProjectionUnavailable("Cross-case graph cursor is invalid.")
    return cursor


def sync_case_graph(store: CaseStore, graph_backend) -> None:
    latest = store.latest_projection_seq()
    workspace_id = store.workspace_id
    cursor = graph_cursor(graph_backend, workspace_id)
    for _ in range(MAX_PROJECTION_PAGES):
        if cursor >= latest:
            break
        snapshots = store.projection_page(cursor, latest, PROJECTION_PAGE_SIZE)
        if not snapshots:
            break
        graph_backend.project(workspace_id, snapshots)
        cursor = graph_cursor(graph_backend, workspace_id)
        if cursor < snapshots[-1]["seq"]:
            break

    if cursor < latest:
        raise ProjectionUnavailable("Cross-case graph is catching up. Retry shortly.")
    if cursor > latest and cursor > store.latest_projection_seq():
        raise ProjectionUnavailable("Cross-case graph is ahead of the source case store.")


ROLE_CAPABILITIES = {
    "viewer": frozenset({"read_cases"}),
    "reviewer": frozenset({"read_cases", "review_cases"}),
    "ingest": frozenset({"ingest_batches"}),
    "admin": frozenset({"read_cases", "review_cases", "ingest_batches"}),
}


@dataclass(frozen=True)
class Analyst:
    analyst_id: str
    role: str


def create_app(
    database: Path,
    analyst_id: str | None = None,
    token: str | None = None,
    *,
    credentials: Mapping[str, Mapping[str, str]] | None = None,
    graph_backend=None,
    public_demo: bool = False,
    bootstrap_demo: bool = False,
    alert_webhook: AlertWebhookConfig | None = None,
    webhook_client_factory=None,
    start_alert_worker: bool = True,
) -> FastAPI:
    if credentials is None:
        if analyst_id is None and token is None and public_demo:
            credentials = {"public-demo": {"role": "viewer", "token": token_urlsafe(48)}}
        else:
            credentials = {analyst_id: {"role": "admin", "token": token}}
    elif analyst_id is not None or token is not None:
        raise ValueError("Use either named credentials or the legacy analyst and token.")
    if not isinstance(credentials, Mapping) or not credentials:
        raise ValueError("Configure at least one named analyst credential.")

    analysts_by_token = {}
    for name, record in credentials.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each analyst must have a nonempty ID.")
        if not isinstance(record, Mapping) or set(record) != {"role", "token"}:
            raise ValueError("Each analyst credential requires a role and a token.")
        role, secret = record["role"], record["token"]
        if not isinstance(role, str) or role not in ROLE_CAPABILITIES:
            raise ValueError("Analyst role must be viewer, reviewer, ingest or admin.")
        if not isinstance(secret, str) or len(secret) < 32 or not secret.isascii():
            raise ValueError("Each analyst needs an ASCII bearer token of at least 32 characters.")
        token_digest = sha256(secret.encode()).digest()
        if token_digest in analysts_by_token:
            raise ValueError("Each analyst must use a different bearer token.")
        analysts_by_token[token_digest] = Analyst(name, role)

    store = CaseStore(database)
    alert_dispatcher = (
        RingAlertDispatcher(store, alert_webhook, webhook_client_factory)
        if alert_webhook is not None else None
    )
    alert_stop = Event()

    @asynccontextmanager
    async def lifespan(_app):
        alert_thread = None
        try:
            if bootstrap_demo:
                fixture = json.loads((Path(__file__).parents[2] / "examples" / "cross-case-review-batches.json").read_text(encoding="utf-8"))
                for batch in fixture["batches"]:
                    store.ingest(
                        batch["batch_id"], batch["transactions"], batch.get("risk_scores", {}),
                        alert_threshold=alert_webhook.threshold if alert_webhook else None,
                    )
            if alert_dispatcher is not None and start_alert_worker:
                alert_thread = Thread(
                    target=alert_dispatcher.run, args=(alert_stop,),
                    name="fraud-ring-alert-outbox", daemon=True,
                )
                alert_thread.start()
            yield
        finally:
            if alert_thread is not None:
                alert_stop.set()
                alert_thread.join(timeout=(alert_webhook.timeout_seconds + 1) if alert_webhook else 6)
            close_graph = getattr(graph_backend, "close", None)
            if close_graph is not None:
                close_graph()

    app = FastAPI(title="Fraud Analyst Case Review", lifespan=lifespan)
    app.state.alert_dispatcher = alert_dispatcher
    app.state.case_store = store
    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> Analyst:
        analyst = None
        if credentials is not None:
            presented = sha256(credentials.credentials.encode()).digest()
            for token_digest, configured_analyst in analysts_by_token.items():
                if compare_digest(token_digest, presented):
                    analyst = configured_analyst
        if analyst is None and public_demo:
            analyst = Analyst("public-demo", "viewer")
        if analyst is None:
            raise HTTPException(status_code=401, detail="An analyst token is required.",
                                headers={"WWW-Authenticate": "Bearer"})
        return analyst

    def require_capability(capability: str):
        def authorize(analyst: Analyst = Depends(authenticate)) -> Analyst:
            if capability not in ROLE_CAPABILITIES[analyst.role]:
                raise HTTPException(status_code=403, detail="This analyst role cannot perform that action.")
            return analyst

        return authorize

    can_read = Depends(require_capability("read_cases"))
    can_ingest = Depends(require_capability("ingest_batches"))
    can_review = Depends(require_capability("review_cases"))

    @app.get("/health")
    def health():
        with store.connect() as db:
            db.execute("SELECT 1 FROM review_cases LIMIT 1").fetchone()
        return {"status": "ok"}

    @app.post("/batches", dependencies=[can_ingest])
    def ingest(request: Intake, response: Response):
        try:
            result = store.ingest(
                request.batch_id, [event.model_dump() for event in request.transactions],
                request.risk_scores,
                alert_threshold=alert_webhook.threshold if alert_webhook else None,
            )
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response.status_code = status.HTTP_201_CREATED if result["created"] else status.HTTP_200_OK
        return result

    @app.get("/cases", dependencies=[can_read])
    def queue(
        state: CaseState | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        after: str = Query(default="", pattern=r"^([a-f0-9]{64})?$"),
    ):
        return {"cases": store.list_cases(state, limit, after)}

    @app.get("/cases/{case_id}", dependencies=[can_read])
    def detail(case_id: str):
        try:
            return store.get_case(case_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Case not found.") from exc

    @app.get("/cases/{case_id}/graph", dependencies=[can_read])
    def graph(
        case_id: str,
        anchor_type: GraphNodeType | None = None,
        anchor_id: Identifier | None = None,
        hops: int = Query(default=2, ge=0, le=4),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if (anchor_type is None) != (anchor_id is None):
            raise HTTPException(status_code=422, detail="Supply both anchor type and anchor ID.")
        try:
            return store.get_case_graph(case_id, anchor_type, anchor_id, hops, limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Case or evidence anchor not found.") from exc

    @app.get("/cases/{case_id}/related-graph", dependencies=[can_read])
    def related_graph(
        case_id: str,
        anchor_type: GraphNodeType | None = None,
        anchor_id: Identifier | None = None,
        hops: int = Query(default=2, ge=0, le=4),
        limit: int = Query(default=100, ge=1, le=200),
    ):
        if (anchor_type is None) != (anchor_id is None):
            raise HTTPException(status_code=422, detail="Supply both anchor type and anchor ID.")
        try:
            anchor = store.get_case_graph(case_id, anchor_type, anchor_id, 0, 1)["anchor"]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Case or evidence anchor not found.") from exc
        if graph_backend is None:
            raise HTTPException(status_code=503, detail="Cross-case graph is not configured.")
        try:
            sync_case_graph(store, graph_backend)
            return graph_backend.related_graph(
                store.workspace_id, case_id, anchor["type"], anchor["id"], hops, limit,
            )
        except ProjectionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc), headers={"Retry-After": "3"}) from exc
        except Exception as exc:
            logger.warning("Cross-case graph unavailable (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Cross-case graph is unavailable.",
                                headers={"Retry-After": "3"}) from exc

    @app.post("/cases/{case_id}/reviews")
    def review(case_id: str, request: Review, analyst: Analyst = can_review):
        try:
            return store.transition(
                case_id, request.version, request.state, analyst.analyst_id, request.reason,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Case not found.") from exc
        except Conflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/analyst/session")
    def session(response: Response, analyst: Analyst = Depends(authenticate)):
        response.headers["Cache-Control"] = "no-store"
        capabilities = ROLE_CAPABILITIES[analyst.role]
        result = {
            "analyst_id": analyst.analyst_id,
            "role": analyst.role,
            "capabilities": sorted(capabilities),
        }
        if graph_backend is not None and "read_cases" in capabilities:
            result["cross_case_available"] = True
        return result

    @app.get("/analyst/demo-batch", dependencies=[can_ingest], include_in_schema=False)
    def demo_batch():
        return FileResponse(
            Path(__file__).parents[2] / "examples" / "case-review-batch.json",
            media_type="application/json",
        )

    @app.get("/analyst/demo-related-batches", dependencies=[can_ingest], include_in_schema=False)
    def demo_related_batches():
        return FileResponse(
            Path(__file__).parents[2] / "examples" / "cross-case-review-batches.json",
            media_type="application/json",
        )

    app.mount("/analyst", StaticFiles(directory=Path(__file__).with_name("analyst"), html=True),
              name="analyst")

    return app


def create_app_from_env() -> FastAPI:
    database = Path(os.getenv("FRAUD_CASE_DATABASE", "case-data/review.sqlite3"))
    graph_backend = None
    graph_uri = os.getenv("FRAUD_CASE_NEO4J_URI", "").strip()
    if graph_uri:
        password = os.getenv("FRAUD_CASE_NEO4J_PASSWORD")
        if not password:
            raise ValueError("FRAUD_CASE_NEO4J_PASSWORD is required when a Neo4j URI is configured.")

        from src.api.case_neo4j import Neo4jCaseGraph

        graph_backend = Neo4jCaseGraph(
            graph_uri, os.getenv("FRAUD_CASE_NEO4J_USER", "neo4j"), password,
            os.getenv("FRAUD_CASE_NEO4J_DATABASE", "neo4j"),
        )
    configured = os.getenv("FRAUD_CASE_CREDENTIALS_JSON")
    alert_webhook = AlertWebhookConfig.from_env()
    if configured:
        try:
            credentials = json.loads(configured)
        except json.JSONDecodeError:
            raise ValueError("FRAUD_CASE_CREDENTIALS_JSON must contain valid JSON.") from None
        if not isinstance(credentials, dict):
            raise ValueError("FRAUD_CASE_CREDENTIALS_JSON must map analyst IDs to credentials.")
        return create_app(
            database, credentials=credentials, graph_backend=graph_backend,
            public_demo=os.getenv("FRAUD_CASE_PUBLIC_DEMO", "").lower() == "true",
            bootstrap_demo=os.getenv("FRAUD_CASE_BOOTSTRAP_DEMO", "").lower() == "true",
            alert_webhook=alert_webhook,
        )
    return create_app(
        database, os.getenv("FRAUD_CASE_ANALYST"), os.getenv("FRAUD_CASE_TOKEN"),
        graph_backend=graph_backend,
        public_demo=os.getenv("FRAUD_CASE_PUBLIC_DEMO", "").lower() == "true",
        bootstrap_demo=os.getenv("FRAUD_CASE_BOOTSTRAP_DEMO", "").lower() == "true",
        alert_webhook=alert_webhook,
    )

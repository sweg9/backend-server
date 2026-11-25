"""Answers `app.request`: one handler per route of openapi/backend-server.yaml, reading and writing the
hub's database (wiki: Hub messages > app.request).

Every route but `POST /login` needs a session: the cookie value is looked up by its SHA-256 in
`sessions` and renewed while used. `POST /login` checks the password against APP_PASSWORD_HASH
(argon2, wiki: Stack > Sign-in). The server only relays and limits login tries.

Changes the resident makes are pushed back to the app as `app.event` through `emit`.
"""

import asyncio
import hashlib
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as Db
from sqlalchemy.orm import sessionmaker

from backend.common.ids import new_id
from backend.common.messages import AppRequest, AppResponse
from backend.common.vocab import DeviceAction, Independence, MappingStatus, Risk, RoutineFeedback, RunResult
from backend.hub.db.models import (
    AppState,
    ChannelRow,
    Control,
    Device,
    Feedback,
    Mapping,
    Routine,
    Run,
    RunChoice,
    Session,
    Situation,
)
from backend.hub.orchestrator import HOME_SPOT, Busy, Orchestrator
from backend.hub.prefs import PROACTIVE_KEY, proactive
from backend.hub.scenario import SCENARIOS
from backend.hub.scenario import start as start_scenario
from backend.hub.views import control_view, device_view, iso, mapping_view, routine_view, run_views, situation_view

log = logging.getLogger(__name__)
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]

TIME_OF_DAY = r"^([01]\d|2[0-3]):[0-5]\d$"


def now() -> datetime:
    return datetime.now(UTC)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, reason: str | None = None):
        self.response = AppResponse(status=status, body={"code": code, "message": message, "reason": reason})


# --- request bodies of the spec ----------------------------------------------------------------


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Body):
    password: str


class DeviceUpdate(Body):
    name: str | None = Field(None, min_length=1)
    room: str | None = Field(None, min_length=1)
    risk: Risk | None = None


class RoutineCreate(Body):
    mapping_id: str
    action: DeviceAction
    time: str | None = Field(None, pattern=TIME_OF_DAY)
    independence: Independence | None = None


class RoutineFeedbackBody(Body):
    decision: RoutineFeedback
    time: str | None = Field(None, pattern=TIME_OF_DAY)
    independence: Independence | None = None


class ProactiveSetting(Body):
    paused: bool


class RouteOrder(Body):
    stops: list[str] = Field(min_length=1, max_length=10)


# --- the handlers ------------------------------------------------------------------------------

Handler = Callable[[Db, AppRequest, dict[str, str]], Awaitable[AppResponse]]


class AppHandlers:
    def __init__(
        self,
        db: sessionmaker,
        session_days: int,
        password_hash: str | None,
        emit: Emit,
        orch: Orchestrator | None = None,
        demo_scenarios: bool = False,
    ):
        self.db = db
        self.orch = orch
        self.demo_scenarios = demo_scenarios
        self.session_days = session_days
        self.password_hash = password_hash
        self.emit = emit
        self._hasher = PasswordHasher()
        self._events: list[tuple[str, dict[str, Any]]] = []
        self._background: set[asyncio.Task] = set()
        self._routes: list[tuple[str, re.Pattern[str], Handler, bool]] = []
        r = self._route
        r("POST", "/login", self.login, signed_in=False)
        r("POST", "/logout", self.logout)
        r("GET", "/me", self.me)
        r("GET", "/controls", self.controls)
        r("GET", "/devices", self.devices)
        r("PATCH", "/devices/{device_id}", self.update_device)
        r("GET", "/mappings", self.mappings)
        r("POST", "/mappings/{mapping_id}/confirm", self.confirm_mapping)
        r("POST", "/mappings/{mapping_id}/reject", self.reject_mapping)
        r("GET", "/routines", self.routines)
        r("POST", "/routines", self.create_routine)
        r("POST", "/routines/{routine_id}/feedback", self.routine_feedback)
        r("GET", "/runs", self.runs)
        r("POST", "/stop", self.stop)
        r("GET", "/proactive", self.get_proactive)
        r("PUT", "/proactive", self.set_proactive)
        r("GET", "/situations", self.situations)
        r("GET", "/situations/{situation_id}", self.situation)
        r("POST", "/situations/{situation_id}/approve", self.approve)
        r("POST", "/situations/{situation_id}/dismiss", self.dismiss)
        r("GET", "/visits", self.visits)
        r("GET", "/robot", self.robot)
        r("POST", "/robot/route", self.route)
        r("POST", "/runs/{run_id}/retry", self.retry)
        r("POST", "/runs/{run_id}/leave", self.leave)
        r("GET", "/scenarios", self.scenarios)
        r("POST", "/scenarios/{name}/start", self.start_scenario)

    def _route(self, method: str, path: str, handler: Handler, signed_in: bool = True) -> None:
        pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
        self._routes.append((method, pattern, handler, signed_in))

    async def handle(self, req: AppRequest) -> AppResponse:
        route, path_known = None, False
        for method, pattern, handler, signed_in in self._routes:
            match = pattern.match(req.path)
            if match is None:
                continue
            path_known = True
            if method == req.method:
                route = (match.groupdict(), handler, signed_in)
                break
        if route is None:
            if path_known:
                return ApiError(405, "method_not_allowed", "Method not allowed").response
            return ApiError(404, "not_found", "No such route").response
        params, handler, signed_in = route
        self._events = []
        try:
            with self.db() as db:
                if signed_in:
                    self._check_session(db, req.session)
                resp = await handler(db, req, params)
                db.commit()
        except ApiError as e:
            return e.response
        except ValidationError as e:
            return ApiError(422, "invalid", "The request does not match the spec", str(e.errors()[:3])).response
        for kind, data in self._events:  # only after the commit, so the app never sees a change that failed
            await self.emit(kind, data)
        return resp

    def _queue(self, kind: str, data: dict[str, Any]) -> None:
        """Handlers never await, so requests cannot interleave between `handle` setting and reading this."""
        self._events.append((kind, data))

    # --- session -----------------------------------------------------------------------------

    def _check_session(self, db: Db, token: str | None) -> Session:
        row = db.get(Session, token_hash(token)) if token else None
        if row is None or row.expires_at <= now():
            raise ApiError(401, "signed_out", "Sign in first")
        row.expires_at = now() + timedelta(days=self.session_days)  # renewed while used
        return row

    async def login(self, db: Db, req: AppRequest, _p) -> AppResponse:
        body = Login.model_validate(req.body or {})
        if not self.password_hash:
            log.error("APP_PASSWORD_HASH is not set: nobody can sign in")
            raise ApiError(401, "wrong_password", "Wrong password")
        try:
            self._hasher.verify(self.password_hash, body.password)
        except (VerificationError, InvalidHashError) as e:
            raise ApiError(401, "wrong_password", "Wrong password") from e
        token = secrets.token_urlsafe(32)
        t = now()
        db.execute(delete(Session).where(Session.expires_at <= t))
        db.add(Session(token_hash=token_hash(token), created_at=t, expires_at=t + timedelta(days=self.session_days)))
        return AppResponse(status=204, set_session=token)

    async def logout(self, db: Db, req: AppRequest, _p) -> AppResponse:
        db.execute(delete(Session).where(Session.token_hash == token_hash(req.session or "")))
        return AppResponse(status=204, set_session="")

    async def me(self, db: Db, req: AppRequest, _p) -> AppResponse:
        row = db.get(Session, token_hash(req.session or ""))
        return AppResponse(status=200, body={"expires_at": iso(row.expires_at)})

    # --- controls and devices ------------------------------------------------------------------

    async def controls(self, db: Db, _req, _p) -> AppResponse:
        rows = db.scalars(select(Control).order_by(Control.created_at)).all()
        return AppResponse(status=200, body=[control_view(c) for c in rows])

    def _channels(self, db: Db) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        rows = db.scalars(select(ChannelRow).order_by(ChannelRow.success_score.desc())).all()
        for c in rows:
            out.setdefault(c.device_id, []).append(c.channel)
        return out

    async def devices(self, db: Db, _req, _p) -> AppResponse:
        channels = self._channels(db)
        rows = db.scalars(select(Device).order_by(Device.created_at)).all()
        return AppResponse(status=200, body=[device_view(d, channels.get(d.device_id, [])) for d in rows])

    async def update_device(self, db: Db, req: AppRequest, p) -> AppResponse:
        body = DeviceUpdate.model_validate(req.body or {})
        d = db.get(Device, p["device_id"])
        if d is None:
            raise ApiError(404, "unknown_device", "Unknown device")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(d, k, v)
        d.updated_at = now()
        return AppResponse(status=200, body=device_view(d, self._channels(db).get(d.device_id, [])))

    # --- mappings ------------------------------------------------------------------------------

    def _mapping(self, db: Db, mapping_id: str) -> Mapping:
        m = db.get(Mapping, mapping_id)
        if m is None:
            raise ApiError(404, "unknown_mapping", "Unknown mapping")
        return m

    async def mappings(self, db: Db, req: AppRequest, _p) -> AppResponse:
        q = select(Mapping).order_by(Mapping.created_at)
        status = req.query.get("status")
        if status is not None:
            if status not in set(MappingStatus):
                raise ApiError(422, "invalid", f"Unknown status: {status}")
            q = q.where(Mapping.status == status)
        return AppResponse(status=200, body=[mapping_view(m) for m in db.scalars(q)])

    async def _set_status(self, db: Db, m: Mapping, status: MappingStatus, reason: str) -> AppResponse:
        m.status = status
        m.status_reason = reason
        m.updated_at = now()
        db.flush()
        self._queue("mapping", mapping_view(m))
        return AppResponse(status=200, body=mapping_view(m))

    async def confirm_mapping(self, db: Db, _req, p) -> AppResponse:
        m = self._mapping(db, p["mapping_id"])
        if m.device_id is None:
            raise ApiError(409, "no_device", "Choose the device first")
        return await self._set_status(db, m, MappingStatus.VERIFIED, "confirmed by the resident")

    async def reject_mapping(self, db: Db, _req, p) -> AppResponse:
        m = self._mapping(db, p["mapping_id"])
        return await self._set_status(db, m, MappingStatus.REJECTED, "rejected by the resident")

    # --- routines ------------------------------------------------------------------------------

    async def routines(self, db: Db, _req, _p) -> AppResponse:
        rows = db.scalars(select(Routine).where(Routine.removed_at.is_(None)).order_by(Routine.priority)).all()
        return AppResponse(status=200, body=[routine_view(r) for r in rows])

    async def create_routine(self, db: Db, req: AppRequest, _p) -> AppResponse:
        body = RoutineCreate.model_validate(req.body or {})
        m = self._mapping(db, body.mapping_id)
        if m.status != MappingStatus.VERIFIED:
            raise ApiError(409, "not_verified", "The mapping is not Verified")  # C-08
        top = db.scalar(select(func.max(Routine.priority)).where(Routine.removed_at.is_(None)))
        t = now()
        r = Routine(
            routine_id=new_id("rt"),
            mapping_id=m.mapping_id,
            action=body.action,
            time=body.time,
            source="manual",
            independence=body.independence or Independence.ASK_FIRST,
            priority=(top or 0) + 1,
            reject_streak=0,
            created_at=t,
            updated_at=t,
        )
        db.add(r)
        db.flush()
        self._queue("routine", routine_view(r))
        return AppResponse(status=201, body=routine_view(r))

    async def routine_feedback(self, db: Db, req: AppRequest, p) -> AppResponse:
        body = RoutineFeedbackBody.model_validate(req.body or {})
        r = db.get(Routine, p["routine_id"])
        if r is None or r.removed_at is not None:
            raise ApiError(404, "unknown_routine", "Unknown routine")
        t = now()
        if body.decision == RoutineFeedback.CHANGE and body.time:
            r.time = body.time
        if body.independence:
            r.independence = body.independence
        if body.decision == RoutineFeedback.REMOVE:
            r.removed_at = t
        r.updated_at = t
        db.add(
            Feedback(
                feedback_id=new_id("fb"),
                routine_id=r.routine_id,
                run_id=None,
                kind=body.decision,
                detail=body.model_dump(exclude_none=True, exclude={"decision"}) or None,
                at=t,
            )
        )
        db.flush()
        event = routine_view(r) | ({"removed": True} if r.removed_at else {})
        self._queue("routine", event)
        return AppResponse(status=200, body=routine_view(r))

    # --- runs and safety -----------------------------------------------------------------------

    async def runs(self, db: Db, req: AppRequest, _p) -> AppResponse:
        q = select(Run).order_by(Run.started_at.desc())
        if "limit" in req.query:
            try:
                limit = int(req.query["limit"])
            except ValueError as e:
                raise ApiError(422, "invalid", "limit is a whole number") from e
            if limit < 1:
                raise ApiError(422, "invalid", "limit is 1 or more")
            q = q.limit(limit)
        return AppResponse(status=200, body=run_views(db, list(db.scalars(q))))

    async def stop(self, _db, _req, _p) -> AppResponse:
        """Only the session check: the server sends `stop` right after this answer (FR-10)."""
        return AppResponse(status=200, body={})

    async def get_proactive(self, db: Db, _req, _p) -> AppResponse:
        return AppResponse(status=200, body=proactive(db))

    async def set_proactive(self, db: Db, req: AppRequest, _p) -> AppResponse:
        body = ProactiveSetting.model_validate(req.body or {})
        row = db.get(AppState, PROACTIVE_KEY)
        value = body.model_dump()
        if row is None:
            db.add(AppState(key=PROACTIVE_KEY, value=value))
        else:
            row.value = value
        return AppResponse(status=200, body=value)

    # --- situations, visits and the robot --------------------------------------------------------

    def _orch(self) -> Orchestrator:
        if self.orch is None:
            raise ApiError(503, "not_running", "The hub's runs are not running")
        return self.orch

    async def situations(self, db: Db, _req, _p) -> AppResponse:
        rows = db.scalars(select(Situation).order_by(Situation.received_at.desc()).limit(50)).all()
        return AppResponse(status=200, body=[situation_view(x) for x in rows])

    def _situation(self, db: Db, situation_id: str) -> dict[str, Any]:
        live = self.orch.situations.get(situation_id) if self.orch else None
        if live is not None:
            return live
        row = db.get(Situation, situation_id)
        if row is None:
            raise ApiError(404, "unknown_situation", "Unknown situation")
        return situation_view(row)

    async def situation(self, db: Db, _req, p) -> AppResponse:
        return AppResponse(status=200, body=self._situation(db, p["situation_id"]))

    async def _answer(self, db: Db, situation_id: str, yes: bool) -> AppResponse:
        sit = self._situation(db, situation_id)
        if not self._orch().answer(situation_id, yes):
            raise ApiError(409, "not_waiting", "This situation is not waiting for an answer")
        return AppResponse(status=200, body=sit)

    async def approve(self, db: Db, _req, p) -> AppResponse:
        return await self._answer(db, p["situation_id"], True)

    async def dismiss(self, db: Db, _req, p) -> AppResponse:
        return await self._answer(db, p["situation_id"], False)

    async def visits(self, _db, _req, _p) -> AppResponse:
        return AppResponse(status=200, body=self._orch().visits)

    async def robot(self, _db, _req, _p) -> AppResponse:
        return AppResponse(status=200, body=self._orch().robot.view())

    def _spot_of(self, db: Db, stop: str) -> str:
        """A stop is a spot id, a control id (its press spot) or a device id (the press spot of the control
        its Verified mapping runs, C-08)."""
        c = db.get(Control, stop)
        if c is None and db.get(Device, stop) is not None:
            m = db.scalar(select(Mapping).where(Mapping.device_id == stop, Mapping.status == MappingStatus.VERIFIED))
            if m is None:
                raise ApiError(409, "not_verified", f"{stop} has no Verified mapping")
            c = db.get(Control, m.control_id)
        if c is not None:
            if not c.press_spot_id:
                raise ApiError(409, "no_press_spot", f"{c.control_id} has no press spot yet")
            return c.press_spot_id
        rows = db.execute(select(Control.seen_spot_id, Control.press_spot_id))
        known = {HOME_SPOT} | {x for row in rows for x in row if x}
        if stop in known:
            return stop
        raise ApiError(404, "unknown_stop", f"Unknown stop: {stop}")

    async def route(self, db: Db, req: AppRequest, _p) -> AppResponse:
        body = RouteOrder.model_validate(req.body or {})
        orch = self._orch()
        spots = [self._spot_of(db, s) for s in body.stops]
        try:
            order = orch.start_route(spots)
        except Busy as e:
            raise ApiError(409, "robot_busy", "The robot is busy", str(e)) from e
        return AppResponse(status=202, body={"route_id": order["route_id"], "stops": spots})

    # --- after a failure, the resident chooses (C-09) ---------------------------------------------

    def _failed(self, db: Db, run_id: str):
        orch = self._orch()
        try:
            run, sit = orch.settled_run(run_id)
        except KeyError as e:
            raise ApiError(404, "unknown_run", "Unknown run") from e
        if run.result == RunResult.SUCCESS:
            raise ApiError(409, "not_failed", "Only a failed, unsure or cancelled run can be retried or left")
        if db.get(RunChoice, run_id) is not None:
            raise ApiError(409, "already_chosen", "The resident already chose for this run")
        item = next((i for i in (sit or {}).get("plan", []) if i["run_id"] == run_id), None)
        return orch, run, sit, item

    async def retry(self, db: Db, _req, p) -> AppResponse:
        orch, run, sit, item = self._failed(db, p["run_id"])
        try:
            ctx = orch.start_retry(db, run, sit)
        except Busy as e:
            raise ApiError(409, "robot_busy", "The robot is busy", str(e)) from e
        if ctx is None:
            raise ApiError(409, "not_verified", "The mapping is no longer Verified, so it cannot be tried again")
        db.add(RunChoice(run_id=run.run_id, choice="retry", retry_run_id=ctx.run_id, chosen_at=now()))
        if item is not None:
            item["resolution"] = "retry"
        return AppResponse(status=202, body={"run_id": run.run_id, "choice": "retry", "retry_run_id": ctx.run_id})

    async def leave(self, db: Db, _req, p) -> AppResponse:
        orch, run, sit, item = self._failed(db, p["run_id"])
        db.add(RunChoice(run_id=run.run_id, choice="leave", retry_run_id=None, chosen_at=now()))
        if item is not None:
            item["resolution"] = "leave"
        task = asyncio.create_task(orch.leave(run, sit))
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return AppResponse(status=200, body={"run_id": run.run_id, "choice": "leave", "retry_run_id": None})

    # --- demo scenarios (scenario.py) ------------------------------------------------------------

    def _demo(self) -> None:
        if not self.demo_scenarios:
            raise ApiError(404, "not_found", "No such route")

    async def scenarios(self, _db, _req, _p) -> AppResponse:
        self._demo()
        return AppResponse(status=200, body=[{"name": k, "description": d} for k, (d, _) in SCENARIOS.items()])

    async def start_scenario(self, _db, req: AppRequest, p) -> AppResponse:
        self._demo()
        if p["name"] not in SCENARIOS:
            raise ApiError(404, "unknown_scenario", f"Unknown scenario: {p['name']}")
        try:
            speed = float(req.query.get("speed", "1"))
        except ValueError as e:
            raise ApiError(422, "invalid", "speed is a number") from e
        if not 0.1 <= speed <= 20:
            raise ApiError(422, "invalid", "speed is from 0.1 to 20")
        try:
            start_scenario(self._orch(), p["name"], speed)
        except Busy as e:
            raise ApiError(409, "robot_busy", "The robot is busy", str(e)) from e
        return AppResponse(status=202, body={"started": p["name"], "speed": speed})

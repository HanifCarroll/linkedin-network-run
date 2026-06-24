"""FastAPI server for the local LinkedIn tools review UI."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from apps.opportunity_intel.contracts import RejectReason, ReviewLabel
from apps.opportunity_intel.store import OpportunityStore
from packages.linkedin_ui import (
    AUTH_FORM_FIELD,
    AUTH_HEADER,
    AUTH_QUERY_PARAM,
    ActionService,
    GuardedCommandActionService,
    LocalAccessToken,
    ReviewAction,
    get_review_action,
    list_review_actions,
)

from .view_models import (
    RankedCommentRow,
    ReviewReadModelProvider,
    SQLiteReviewReadModelProvider,
)

TEMPLATE_DIR = Path(__file__).with_name("templates")
STATIC_DIR = Path(__file__).with_name("static")


def create_app(
    *,
    provider: ReviewReadModelProvider | None = None,
    action_service: ActionService | None = None,
    access_token: str | None = None,
    opportunity_store: OpportunityStore | None = None,
) -> FastAPI:
    store = opportunity_store or OpportunityStore()
    read_models = provider or SQLiteReviewReadModelProvider(store=store)
    actions = list_review_actions()
    service = action_service or GuardedCommandActionService()
    gate = (
        LocalAccessToken(access_token)
        if access_token is not None
        else LocalAccessToken.generate()
    )

    app = FastAPI(title="LinkedIn Tools Review UI")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.globals["command_text"] = _command_text
    templates.env.globals["token_param"] = AUTH_QUERY_PARAM
    app.state.local_access_token = gate.token

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def context(
        request: Request,
        *,
        section: str,
        access_token_value: str | None,
        message: str | None = None,
    ) -> dict[str, object]:
        snapshot = read_models.snapshot()
        return {
            "request": request,
            "section": section,
            "snapshot": snapshot,
            "actions": actions,
            "access_token": access_token_value,
            "auth_form_field": AUTH_FORM_FIELD,
            "message": message,
            "nav_items": _nav_items(),
        }

    async def page_context(
        request: Request,
        *,
        section: str,
        message: str | None = None,
    ) -> dict[str, object]:
        return context(
            request,
            section=section,
            access_token_value=await _provided_token(request),
            message=message,
        )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            await page_context(request, section="dashboard"),
        )

    @app.get("/opportunities", response_class=HTMLResponse)
    async def opportunities(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "opportunities.html",
            await page_context(request, section="opportunities"),
        )

    @app.get("/network", response_class=HTMLResponse)
    async def network(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "network.html",
            await page_context(request, section="network"),
        )

    @app.get("/recruiter-agency", response_class=HTMLResponse)
    async def recruiter_agency(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "recruiter_agency.html",
            await page_context(request, section="recruiter"),
        )

    @app.get("/browser", response_class=HTMLResponse)
    async def browser(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "browser.html",
            await page_context(request, section="browser"),
        )

    @app.get("/actions", response_class=HTMLResponse)
    async def guarded_actions(request: Request) -> Response:
        supplied = await _require_token(request, gate)
        return templates.TemplateResponse(
            request,
            "actions.html",
            context(request, section="actions", access_token_value=supplied),
        )

    @app.get("/partials/opportunities/comments", response_class=HTMLResponse)
    async def opportunity_comment_rows(
        request: Request,
        level: str | None = None,
    ) -> Response:
        rows = read_models.snapshot().ranked_comments
        if level:
            rows = tuple(row for row in rows if row.level == level)
        return templates.TemplateResponse(
            request,
            "partials/opportunity_comments.html",
            {
                "request": request,
                "rows": rows,
                "access_token": await _provided_token(request),
                "auth_form_field": AUTH_FORM_FIELD,
            },
        )

    @app.get("/partials/network/queue", response_class=HTMLResponse)
    async def network_queue(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "partials/network_queue.html",
            {
                "request": request,
                "snapshot": read_models.snapshot(),
            },
        )

    @app.get("/partials/recruiter-agency/leads", response_class=HTMLResponse)
    async def recruiter_leads(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "partials/recruiter_leads.html",
            {
                "request": request,
                "snapshot": read_models.snapshot(),
            },
        )

    @app.get("/partials/browser/artifacts", response_class=HTMLResponse)
    async def browser_artifacts(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "partials/browser_artifacts.html",
            {
                "request": request,
                "snapshot": read_models.snapshot(),
            },
        )

    @app.post("/opportunities/comments/{comment_id}/label", response_class=HTMLResponse)
    async def label_comment(request: Request, comment_id: str) -> Response:
        supplied = await _require_token(request, gate)
        form = await request.form()
        label = _string_form_value(form, "label")
        try:
            review_label = ReviewLabel(label or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unsupported label") from exc
        reject_reason_value = _string_form_value(form, "reject_reason") or ""
        reject_reason = _reject_reason(reject_reason_value)
        notes = _string_form_value(form, "notes") or ""
        row = _find_comment(read_models.snapshot().ranked_comments, comment_id)
        try:
            store.set_review_label(
                comment_key=comment_id,
                label=review_label,
                reject_reason=reject_reason,
                notes=notes,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        message = f"{row.commenter} marked {review_label.value}"
        return templates.TemplateResponse(
            request,
            "partials/action_result.html",
            {
                "request": request,
                "result": {
                    "status": "recorded",
                    "message": message,
                    "command": "",
                    "warnings": (),
                },
                "access_token": supplied,
            },
        )

    @app.post("/actions/{action_id}", response_class=HTMLResponse)
    async def run_guarded_action(request: Request, action_id: str) -> Response:
        supplied = await _require_token(request, gate)
        try:
            action = get_review_action(action_id, actions)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        result = service.execute(action)
        return templates.TemplateResponse(
            request,
            "partials/action_result.html",
            {
                "request": request,
                "result": {
                    "status": result.status,
                    "message": result.message,
                    "command": _command_text(result.command),
                    "warnings": result.warnings,
                },
                "access_token": supplied,
            },
        )

    return app


def _nav_items() -> tuple[dict[str, str], ...]:
    return (
        {"id": "dashboard", "href": "/", "label": "Overview"},
        {"id": "opportunities", "href": "/opportunities", "label": "Opportunities"},
        {"id": "network", "href": "/network", "label": "Network"},
        {"id": "recruiter", "href": "/recruiter-agency", "label": "Recruiter/Agency"},
        {"id": "browser", "href": "/browser", "label": "Browser"},
        {"id": "actions", "href": "/actions", "label": "Guarded Actions"},
    )


def _command_text(argv: Iterable[str]) -> str:
    return " ".join(argv)


async def _provided_token(request: Request) -> str | None:
    header_token = request.headers.get(AUTH_HEADER)
    if header_token:
        return header_token
    query_token = request.query_params.get(AUTH_QUERY_PARAM)
    if query_token:
        return query_token
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    form = await request.form()
    return _string_form_value(form, AUTH_FORM_FIELD)


async def _require_token(request: Request, gate: LocalAccessToken) -> str:
    supplied = await _provided_token(request)
    if not gate.verify(supplied):
        raise HTTPException(status_code=403, detail="Local access token required")
    return supplied or ""


def _string_form_value(form: FormData, key: str) -> str | None:
    value = form.get(key)
    return value if isinstance(value, str) else None


def _find_comment(rows: Iterable[RankedCommentRow], comment_id: str) -> RankedCommentRow:
    for row in rows:
        if row.comment_id == comment_id:
            return row
    raise HTTPException(status_code=404, detail="Comment not found")


def _reject_reason(value: str) -> RejectReason | None:
    if not value:
        return None
    try:
        return RejectReason(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsupported reject reason") from exc


def action_is_enabled(action: ReviewAction) -> bool:
    return action.enabled

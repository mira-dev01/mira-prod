"""Control panel for the test services (app/integrations/payment_simulator.py). 404 unless
SIMULATOR_ENABLED is on; when SIMULATOR_TOKEN is set, control calls must send it as X-Sim-Token.

The guest-side pay page (GET /sim/pay/{link_id}) is what a "payment link" opens in test mode: it lets a
person pay, fail an attempt or close the link, with the same wording a real checkout would show."""

import html

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.config import settings
from app.integrations import payment_simulator as sim
from app.integrations.razorpay_client import RazorpayError

router = APIRouter(prefix="/sim", tags=["simulator"])

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Test checkout</title><style>body{{font-family:system-ui,sans-serif;background:#f3ede2;color:#2a2420;margin:0;display:grid;place-items:center;min-height:100vh}}
main{{background:#fbf7ef;border:1px solid #2a2420;padding:24px;max-width:420px;width:calc(100% - 32px)}}h1{{font-size:1.25rem;margin:0 0 8px}}
p{{margin:8px 0}}small{{color:#6f6252}}form{{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}}
button{{font:inherit;padding:8px 14px;border:1px solid #2a2420;background:#fbf7ef;cursor:pointer}}button.primary{{background:#b8452f;color:#fff;border-color:#b8452f}}</style></head>
<body><main><small>TEST CHECKOUT: no real money</small>{body}</main></body></html>"""


def _require_enabled() -> None:
    if not settings.simulator_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def require_control(x_sim_token: str | None = Header(default=None)) -> None:
    _require_enabled()
    if settings.simulator_token and x_sim_token != settings.simulator_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or wrong X-Sim-Token")


class OutageIn(BaseModel):
    target: str
    down: bool


class OutcomeIn(BaseModel):
    outcome: str


@router.get("/state", dependencies=[Depends(require_control)])
async def state() -> dict:
    return {"down": await sim.flags(), "targets": list(sim.TARGETS), "outcomes": list(sim.OUTCOMES)}


@router.post("/outage", dependencies=[Depends(require_control)])
async def outage(payload: OutageIn) -> dict:
    """Switch a pretend service down or back up: payment, messaging or calendar."""
    if payload.target not in sim.TARGETS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"target must be one of {', '.join(sim.TARGETS)}")
    await sim.set_down(payload.target, payload.down)
    return {"down": await sim.flags()}


@router.post("/payments/{link_id}", dependencies=[Depends(require_control)])
async def payment_outcome(link_id: str, payload: OutcomeIn) -> dict:
    """What the guest does with a payment link: paid, failed (an attempt that did not go through), cancelled,
    expired, or reset (open it again)."""
    try:
        link = await sim.set_outcome(link_id, payload.outcome)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    except RazorpayError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT if exc.status_code != 404 else status.HTTP_404_NOT_FOUND, str(exc))
    return {"id": link.id, "status": link.status, "last_attempt": link.last_attempt}


@router.get("/messages", dependencies=[Depends(require_control)])
async def messages(limit: int = 50) -> list[dict]:
    """The WhatsApp messages the test service "sent", newest first."""
    return await sim.recent_messages(min(max(limit, 1), 200))


# -- the guest's side of a payment link -------------------------------------------------------------------


def _page(body: str, code: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=code)


@router.get("/pay/{link_id}")
async def pay_page(link_id: str) -> HTMLResponse:
    _require_enabled()
    try:
        link = await sim.fetch_payment_link(link_id)
    except RazorpayError as exc:
        message = "Online payment is temporarily unavailable." if "unavailable" in str(exc) else "We couldn't find this booking"
        return _page(f"<h1>{html.escape(message)}</h1>", 503 if "unavailable" in message else 404)
    amount = f"₹{(link.amount_paise or 0) / 100:,.0f}"
    if link.status == "paid":
        return _page("<h1>This booking is already paid</h1>")
    if link.status == "cancelled":
        return _page("<h1>This reservation was cancelled</h1>")
    if link.status == "expired":
        return _page("<h1>This payment link has expired</h1><p>Open My Bookings to see your booking.</p>")
    notice = (
        "<p><strong>Payment unsuccessful.</strong> Almost there! Your villa is held, just one more try needed to finish booking your stay.</p>"
        if link.last_attempt == "failed"
        else ""
    )
    return _page(
        f"<h1>Pay {amount}</h1>{notice}<p>Choose what happens next:</p>"
        f'<form method="post" action="/api/v1/sim/pay/{html.escape(link_id)}/paid"><button class="primary">Pay {amount}</button></form>'
        f'<form method="post" action="/api/v1/sim/pay/{html.escape(link_id)}/failed"><button>Fail this attempt</button></form>'
        f'<form method="post" action="/api/v1/sim/pay/{html.escape(link_id)}/cancelled"><button>Close without paying</button></form>'
    )


@router.post("/pay/{link_id}/{outcome}")
async def pay_action(link_id: str, outcome: str) -> HTMLResponse:
    _require_enabled()
    if outcome not in ("paid", "failed", "cancelled"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        await sim.set_outcome(link_id, outcome)
    except RazorpayError as exc:
        return _page(f"<h1>{html.escape(str(exc))}</h1>", 409)
    if outcome == "paid":
        return _page("<h1>Payment successful</h1><p>You can close this page. Your confirmation is on its way on WhatsApp.</p>")
    if outcome == "failed":
        return _page(
            "<h1>Payment unsuccessful</h1><p>Almost there! Your villa is held, just one more try needed to finish booking your stay.</p>"
            f'<form method="get" action="/api/v1/sim/pay/{html.escape(link_id)}"><button class="primary">Try again</button></form>'
        )
    return _page("<h1>This reservation was cancelled</h1>")

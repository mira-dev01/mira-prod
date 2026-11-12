"""Mira x WhatsApp: a live monitor of the WhatsApp messages the agent sends and the guests send back.

Reads Twilio's own record of the Mira WhatsApp number through the deployed Mira backend
(GET /api/v1/whatsapp-monitor/messages) and shows it the way a guest would see it: one conversation per guest
number, chat bubbles, the delivery status of every message (sent / delivered / read / failed, with Twilio's error
code), and a progress tracker of the booking journey (payment link -> payment -> booking confirmed -> stay code).
Because it shows Twilio's record, it is also the quickest way to see when the WhatsApp connection itself has a
problem (a message Twilio accepted but could not deliver). Nothing here writes anything; it only reads.

One-time setup on the backend (Railway): set WHATSAPP_MONITOR_TOKEN to a long random string and redeploy. Until it
is set the endpoint answers 404 and this page says so.

Run it from a terminal (needs Python 3.9+):

    pip install streamlit requests
    export MIRA_BACKEND_URL="https://<your-railway-backend-domain>"
    export WHATSAPP_MONITOR_TOKEN="<the WHATSAPP_MONITOR_TOKEN set on Railway>"
    streamlit run whatsapp_simulation.py

or pass them on the command line (after the double dash):

    streamlit run whatsapp_simulation.py -- --base-url https://<domain> --token <token>

Both can also be typed into the sidebar. The token is never printed or stored.
"""

import argparse
import html
import os
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import requests
import streamlit as st

try:  # Python 3.9+
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - a platform without tz data falls back to a fixed offset
    IST = timezone(timedelta(hours=5, minutes=30))

st.set_page_config(page_title="Mira x WhatsApp monitor", page_icon="💬", layout="wide")


# ---------------------------------------------------------------------------------------------------------------
# Settings: environment, then command line, then whatever is typed in the sidebar
# ---------------------------------------------------------------------------------------------------------------


def _cli_defaults() -> tuple[str, str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--base-url", default=os.environ.get("MIRA_BACKEND_URL", ""))
    parser.add_argument("--token", default=os.environ.get("WHATSAPP_MONITOR_TOKEN", ""))
    known, _ = parser.parse_known_args(sys.argv[1:])
    return known.base_url, known.token


DEFAULT_URL, DEFAULT_TOKEN = _cli_defaults()


# ---------------------------------------------------------------------------------------------------------------
# What a message is (recognised from its wording; Twilio's record holds who, text, status and time)
# ---------------------------------------------------------------------------------------------------------------

KINDS = OrderedDict(
    [
        ("payment_link", ("💳", "Payment link", "#1f6feb")),
        ("payment_failed", ("↩️", "Payment unsuccessful", "#bc4c00")),
        ("confirmation", ("✅", "Booking confirmation", "#1a7f37")),
        ("shoot_held", ("🎬", "Dates held (shoot)", "#1a7f37")),
        ("stay_request", ("🕒", "Early check-in / late check-out", "#8250df")),
        ("photos", ("🖼️", "Photos / property list", "#0969da")),
        ("guest", ("👤", "Guest", "#57606a")),
        ("other", ("💬", "Message", "#57606a")),
    ]
)

URL_RE = re.compile(r"(https?://[^\s<>\"']+)")

# What Twilio's error codes mean for this workflow (shown next to a message that was not delivered).
ERROR_HINTS = {
    63016: "Outside WhatsApp's 24-hour window: this message needs an approved template (see whatsapp_templates.py).",
    63015: "The guest's number is not reachable on WhatsApp.",
    63024: "Not a valid WhatsApp number.",
    63049: "WhatsApp declined this message (Meta marketing limits).",
    63003: "The recipient could not be reached on WhatsApp.",
    63007: "The WhatsApp sender is not set up correctly on Twilio.",
    21211: "Not a valid phone number.",
    21608: "The Twilio account is a trial: the recipient must be verified first.",
}


def classify(message: dict) -> str:
    if message.get("direction") == "inbound":
        return "guest"
    body = (message.get("body") or "").lower()
    if "payment unsuccessful" in body:
        return "payment_failed"
    if "your booking is now confirmed" in body or "booking details is following" in body:
        return "confirmation"
    if "payment successful" in body and "dates are held" in body:
        return "shoot_held"
    if "complete your spacez booking payment" in body or "please pay here" in body or "pay here" in body:
        return "payment_link" if "early check-in" not in body and "late check-out" not in body else "stay_request"
    if "early check-in" in body or "late check-out" in body:
        return "stay_request"
    if "photos of" in body or "stays we talked about" in body or "/p/" in body:
        return "photos"
    return "other"


def guest_number(message: dict) -> str:
    return (message.get("from") if message.get("direction") == "inbound" else message.get("to")) or "unknown"


def parse_time(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(IST)
    except Exception:
        return datetime.now(IST)


def mask(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) <= 4:
        return phone
    return "+" + "•" * max(0, len(digits) - 4) + digits[-4:]


def day_label(moment: datetime) -> str:
    today = datetime.now(IST).date()
    if moment.date() == today:
        return "Today"
    if moment.date() == today - timedelta(days=1):
        return "Yesterday"
    return moment.strftime("%d %b %Y")


def is_failed(message: dict) -> bool:
    return message.get("status") in ("failed", "undelivered")


# ---------------------------------------------------------------------------------------------------------------
# Talking to the backend
# ---------------------------------------------------------------------------------------------------------------


class BackendError(Exception):
    """A problem the person at the screen can act on."""


def fetch_messages(base_url: str, token: str, limit: int) -> list[dict]:
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        raise BackendError("Enter the backend URL in the sidebar (your Railway backend, not the Vercel dashboard).")
    if not re.match(r"^https?://", base_url):
        base_url = "https://" + base_url
    headers = {"X-Monitor-Token": token} if token else {}
    try:
        response = requests.get(f"{base_url}/api/v1/whatsapp-monitor/messages", params={"limit": limit}, headers=headers, timeout=20)
    except requests.exceptions.ConnectionError:
        raise BackendError(f"Could not reach {base_url}. Check the URL and that the backend is running.")
    except requests.exceptions.Timeout:
        raise BackendError("The backend took too long to answer. Try again in a moment.")
    except requests.exceptions.RequestException as exc:  # invalid URL and similar
        raise BackendError(f"Could not use that URL: {exc}")
    if response.status_code == 404:
        raise BackendError(
            "The monitor is not switched on for that backend (HTTP 404). Set WHATSAPP_MONITOR_TOKEN on the Railway "
            "environment, redeploy, and check the URL points at the backend."
        )
    if response.status_code == 401:
        raise BackendError("The token was refused (HTTP 401). Use the WHATSAPP_MONITOR_TOKEN set on that backend.")
    if response.status_code == 502:
        raise BackendError("The backend could not get an answer from Twilio (HTTP 502). Check the Twilio credentials on the backend.")
    if response.status_code >= 400:
        raise BackendError(f"The backend answered HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError:
        raise BackendError("The backend did not return JSON. Check the URL.")
    if not isinstance(data, list):
        raise BackendError("Unexpected answer from the backend.")
    return data


def conversations_from(messages: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Messages grouped by guest number, oldest first inside each, conversations ordered by latest activity."""
    grouped: dict[str, list[dict]] = {}
    for message in sorted(messages, key=lambda m: str(m.get("at") or "")):
        grouped.setdefault(guest_number(message), []).append(message)
    return OrderedDict(sorted(grouped.items(), key=lambda kv: str(kv[1][-1].get("at") or ""), reverse=True))


# ---------------------------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------------------------

CSS = """
<style>
.block-container { padding-top: 1.4rem; }
.hero { display:flex; align-items:center; gap:14px; margin-bottom:4px; }
.hero h1 { margin:0; font-size:1.7rem; font-weight:700; letter-spacing:-0.02em; }
.badge { font-size:.72rem; font-weight:700; letter-spacing:.08em; padding:3px 9px; border-radius:99px;
         background:#dafbe1; color:#116329; border:1px solid #a6e3b4; }
.sub { color:#57606a; margin:0 0 14px 0; }
.chat-shell { border:1px solid #d0d7de; border-radius:14px; overflow:hidden; background:#efeae2; }
.chat-head { background:#008069; color:#fff; padding:12px 16px; display:flex; align-items:center; gap:12px; }
.avatar { width:38px; height:38px; border-radius:50%; background:#cfe9e3; color:#005c4b; display:flex;
          align-items:center; justify-content:center; font-weight:700; }
.chat-head .who { font-weight:600; line-height:1.15; }
.chat-head .meta { font-size:.78rem; opacity:.85; }
.chat-body { padding:14px 16px 18px; max-height:700px; overflow-y:auto; display:flex; flex-direction:column-reverse;
             background-image: radial-gradient(#e3dccd 1px, transparent 1px); background-size: 18px 18px; }
.day { text-align:center; margin:10px 0; }
.day span { background:#fff; color:#54656f; font-size:.74rem; padding:3px 10px; border-radius:8px;
            box-shadow:0 1px 1px rgba(0,0,0,.08); }
.row { display:flex; justify-content:flex-end; margin:6px 0; }
.row.in { justify-content:flex-start; }
.bubble { max-width:78%; background:#d9fdd3; color:#111b21; border-radius:10px 10px 2px 10px; padding:8px 11px 6px;
          box-shadow:0 1px 1px rgba(0,0,0,.12); font-size:.93rem; }
.row.in .bubble { background:#fff; border-radius:10px 10px 10px 2px; }
.bubble.failed { background:#ffe3e0; border:1px solid #f4b6af; }
.tag { display:inline-block; font-size:.68rem; font-weight:700; letter-spacing:.04em; padding:1px 7px; border-radius:6px;
       color:#fff; margin-bottom:5px; }
.text { white-space:pre-wrap; word-break:break-word; line-height:1.38; }
.text a { color:#0b57d0; text-decoration:underline; }
.foot { text-align:right; font-size:.7rem; color:#667781; margin-top:3px; }
.foot .bad { color:#b3261e; font-weight:700; }
.foot .read { color:#2f8be6; font-weight:700; }
.hint { font-size:.74rem; color:#8a3b12; margin-top:4px; text-align:left; }
.steps { display:flex; gap:8px; margin:6px 0 14px; flex-wrap:wrap; }
.step { flex:1; min-width:150px; border:1px solid #d0d7de; border-radius:12px; padding:10px 12px; background:#fff; }
.step .n { font-size:.7rem; color:#6e7781; font-weight:700; letter-spacing:.06em; }
.step .t { font-weight:600; margin-top:2px; }
.step .s { font-size:.78rem; color:#57606a; margin-top:2px; }
.step.done { border-color:#1a7f37; background:#f0fbf3; }
.step.done .n { color:#1a7f37; }
.step.fail { border-color:#cf222e; background:#fff1f0; }
.step.fail .n { color:#cf222e; }
.step.warn { border-color:#bc4c00; background:#fff8f0; }
.step.warn .n { color:#bc4c00; }
.empty { border:1px dashed #b6bec6; border-radius:14px; padding:28px; background:#fafbfc; color:#424a53; }
</style>
"""


def linkify(text: str) -> str:
    escaped = html.escape(text)
    return URL_RE.sub(lambda m: f'<a href="{m.group(1)}" target="_blank" rel="noopener">{m.group(1)}</a>', escaped)


def display_text(message: dict) -> str:
    body = message.get("body") or ""
    if not body.strip():
        media = int(message.get("num_media") or 0)
        return f"📎 {media} attachment{'s' if media != 1 else ''}" if media else "(Twilio did not return this message's text)"
    return body


def status_html(message: dict) -> str:
    status = message.get("status")
    if is_failed(message):
        code = message.get("error_code")
        return f'<span class="bad">⚠ Not delivered{f" (error {code})" if code else ""}</span>'
    if message.get("direction") == "inbound":
        return "Received"
    return {
        "read": '<span class="read">✓✓ Read</span>',
        "delivered": "✓✓ Delivered",
        "sent": "✓ Sent",
        "queued": "… Queued",
        "accepted": "… Queued",
        "sending": "… Sending",
    }.get(status or "", html.escape(str(status or "")))


def bubble(message: dict) -> str:
    kind = classify(message)
    icon, label, colour = KINDS[kind]
    failed = is_failed(message)
    inbound = message.get("direction") == "inbound"
    moment = parse_time(message.get("at"))
    hint = ""
    if failed:
        text = ERROR_HINTS.get(message.get("error_code")) or message.get("error_message") or "Twilio could not deliver this message."
        hint = f'<div class="hint">{html.escape(str(text))}</div>'
    return (
        f'<div class="row{" in" if inbound else ""}"><div class="bubble{" failed" if failed else ""}">'
        f'<span class="tag" style="background:{colour}">{icon} {label}</span>'
        f'<div class="text">{linkify(display_text(message))}</div>{hint}'
        f'<div class="foot">{moment.strftime("%I:%M %p").lstrip("0")} &nbsp; {status_html(message)}</div>'
        f"</div></div>"
    )


def chat_html(number: str, messages: list[dict], show_full_numbers: bool) -> str:
    shown = number if show_full_numbers else mask(number)
    parts = [
        '<div class="chat-shell"><div class="chat-head">'
        '<div class="avatar">G</div>'
        f'<div><div class="who">Guest {html.escape(shown)}</div>'
        f'<div class="meta">Messages between Mira and the guest &nbsp;·&nbsp; {len(messages)} message'
        f'{"s" if len(messages) != 1 else ""}</div></div></div><div class="chat-body"><div class="chat-inner">'
    ]
    last_day = None
    for message in messages:
        day = day_label(parse_time(message.get("at")))
        if day != last_day:
            parts.append(f'<div class="day"><span>{html.escape(day)}</span></div>')
            last_day = day
        parts.append(bubble(message))
    parts.append("</div></div></div>")
    return "".join(parts)


def journey(messages: list[dict]) -> list[tuple[str, str, str, str]]:
    """(number, title, detail, state) for each step of the booking journey, read from what was sent. A receipt is
    only ever sent after the payment provider reports paid, so a confirmation (or 'dates held') message means 'paid'."""
    outbound = [m for m in messages if m.get("direction") != "inbound"]
    kinds = [classify(m) for m in outbound]
    link = next((m for m, k in zip(outbound, kinds) if k == "payment_link"), None)
    retry = next((m for m, k in zip(outbound, kinds) if k == "payment_failed"), None)
    receipt = next((m for m, k in zip(outbound, kinds) if k in ("confirmation", "shoot_held")), None)
    receipt_kind = classify(receipt) if receipt else None

    def at(m):
        return parse_time(m.get("at")).strftime("%I:%M %p").lstrip("0") if m else ""

    def delivered(m):
        return not is_failed(m)

    steps = [
        (
            "1",
            "Payment link sent",
            (f"Sent at {at(link)}" if delivered(link) else "WhatsApp did not deliver it") if link else "Waiting for the agent to send the link",
            ("done" if delivered(link) else "fail") if link else "",
        ),
        (
            "2",
            "Payment received",
            "Confirmed by the payment provider" if receipt else ("Guest's payment failed; same link re-sent" if retry else "Waiting for the guest to pay"),
            "done" if receipt else ("warn" if retry else ""),
        ),
        (
            "3",
            "Dates held (shoot)" if receipt_kind == "shoot_held" else "Booking confirmed",
            (f"Sent at {at(receipt)}" if delivered(receipt) else "WhatsApp did not deliver it") if receipt else "Sent once payment succeeds",
            ("done" if delivered(receipt) else "fail") if receipt else "",
        ),
        (
            "4",
            "Stay code shared",
            "Sent with the confirmation" if receipt and "stay code:" in (receipt.get("body") or "").lower() else "Sent with the confirmation",
            "done" if receipt and "stay code:" in (receipt.get("body") or "").lower() else "",
        ),
    ]
    if any(is_failed(m) for m in outbound):
        steps.append(("!", "Delivery problem", "At least one message could not be delivered", "fail"))
    return steps


def steps_html(steps: list[tuple[str, str, str, str]]) -> str:
    cards = "".join(
        f'<div class="step {state}"><div class="n">{"ATTENTION" if n == "!" else f"STEP {n}"}</div>'
        f'<div class="t">{html.escape(title)}</div><div class="s">{html.escape(detail)}</div></div>'
        for n, title, detail, state in steps
    )
    return f'<div class="steps">{cards}</div>'


# ---------------------------------------------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------------------------------------------

st.markdown(CSS, unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Connection")
    base_url = st.text_input("Backend URL", value=DEFAULT_URL, placeholder="https://your-backend.up.railway.app")
    token = st.text_input("Monitor token", value=DEFAULT_TOKEN, type="password", help="The WHATSAPP_MONITOR_TOKEN set on the backend.")
    limit = st.slider("Messages to load", 20, 300, 100, step=20)
    st.markdown("### View")
    auto = st.toggle("Refresh automatically", value=True)
    every = st.select_slider("Every", options=[5, 10, 20, 30, 60], value=10, format_func=lambda s: f"{s}s", disabled=not auto)
    full_numbers = st.toggle("Show full phone numbers", value=False, help="Off by default so a screen share does not expose guest numbers.")
    st.markdown("### How the workflow runs")
    st.markdown(
        "1. The guest calls and the agent quotes the stay.\n"
        "2. The guest gives their details and chooses how to pay.\n"
        "3. The agent holds the dates and **sends the payment link on WhatsApp**, with the calculation of the grand total.\n"
        "4. The guest pays on the Razorpay page.\n"
        "5. The agent checks the payment; only then are the **booking confirmation and stay code** sent.\n"
        "6. If the guest asks, the agent also sends **photos or the list of recommended stays**."
    )
    st.caption("This page only reads Twilio's record. Messages marked red were accepted by Twilio but not delivered.")

st.markdown(
    '<div class="hero"><h1>Mira × WhatsApp</h1><span class="badge">LIVE · TWILIO</span></div>'
    '<p class="sub">Every WhatsApp message the booking agent sends and the guest sends back, with its delivery status.</p>',
    unsafe_allow_html=True,
)

try:
    messages = fetch_messages(base_url, token, limit)
    error = None
except BackendError as exc:
    messages, error = [], str(exc)

if error:
    st.error(error)
    st.stop()

grouped = conversations_from(messages)
outbound_count = sum(1 for m in messages if m.get("direction") != "inbound")
total_failed = sum(1 for m in messages if is_failed(m))
confirmed = sum(1 for m in messages if classify(m) in ("confirmation", "shoot_held"))
links = sum(1 for m in messages if classify(m) == "payment_link")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Conversations", len(grouped))
c2.metric("Messages sent by Mira", outbound_count)
c3.metric("Payment links → receipts", f"{links} → {confirmed}")
c4.metric("Delivery failures", total_failed)

if not messages:
    st.markdown(
        '<div class="empty"><b>No WhatsApp messages yet.</b><br>Take a call with the booking agent as far as '
        "sending a payment link. Its WhatsApp messages will appear here within a few seconds.</div>",
        unsafe_allow_html=True,
    )
else:
    numbers = list(grouped.keys())
    labels = {
        n: f"{n if full_numbers else mask(n)}  ·  {len(grouped[n])} msg · last {parse_time(grouped[n][-1].get('at')).strftime('%I:%M %p').lstrip('0')}"
        for n in numbers
    }
    left, right = st.columns([1, 2.4], gap="large")
    with left:
        st.markdown("#### Conversations")
        chosen = st.radio("Conversation", numbers, format_func=lambda n: labels[n], label_visibility="collapsed")
        st.caption("Newest activity first.")
    with right:
        st.markdown("#### Booking journey")
        st.markdown(steps_html(journey(grouped[chosen])), unsafe_allow_html=True)
        st.markdown(chat_html(chosen, grouped[chosen], full_numbers), unsafe_allow_html=True)
        with st.expander("Show Twilio's raw record for this conversation"):
            st.json(grouped[chosen])

st.caption(f"Last updated {datetime.now(IST).strftime('%I:%M:%S %p').lstrip('0')} IST · reading {(base_url or '—').rstrip('/')}")

if auto:
    time.sleep(every)
    st.rerun()

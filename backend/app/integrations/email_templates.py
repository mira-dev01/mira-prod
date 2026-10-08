"""HTML templates for host-facing transactional emails.

Inline CSS only (no <style> block) -- table-based layout with attribute/
inline styling is the one approach that renders consistently across Gmail,
Outlook, and mobile mail clients, all of which strip or mangle <style>
tags to varying degrees. Colors are pulled 1:1 from the dashboard's palette
(frontend/src/app/globals.css) so this reads as the same product.
"""

import re

from app.config import settings

_PRIMARY = "#d94f3d"
_BACKGROUND = "#f5f0e8"
_CARD = "#fdfaf5"
_FOREGROUND = "#1a1714"
_MUTED = "#635747"
_BORDER = "#e8e0d5"

_URGENCY_COLORS = {
    "emergency": "#d94f3d",
    "high": "#d94f3d",
    "medium": "#e8a838",
    "low": "#6b7280",
}


def _whatsapp_link(guest_phone: str) -> str | None:
    digits = re.sub(r"\D", "", guest_phone)
    if not digits:
        return None
    # Exotel numbers are stored/spoken in Indian local formats; wa.me needs
    # a bare country-code-prefixed number with no +/spaces/punctuation.
    if not digits.startswith("91") and len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}"


_TEMPERATURE_COLORS = {
    "hot": "#d94f3d",
    "warm": "#e8a838",
    "cold": "#6b7280",
}


def build_escalation_email_html(
    *,
    property_name: str,
    urgency: str,
    reason: str,
    call_summary: str | None,
    guest_phone: str | None,
    call_session_id: object | None = None,
    lead_temperature: str | None = None,
) -> str:
    urgency_color = _URGENCY_COLORS.get(urgency, "#6b7280")
    # Deep-links to the specific call's transcript + AI summary when known,
    # rather than the generic leads list -- a host clicking through from an
    # escalation email almost always wants THIS call, not the whole pipeline.
    dashboard_url = (
        f"{settings.frontend_base_url}/dashboard/calls/{call_session_id}"
        if call_session_id
        else f"{settings.frontend_base_url}/dashboard/leads"
    )
    whatsapp_url = _whatsapp_link(guest_phone) if guest_phone else None

    badges = [
        f'<span style="display:inline-block;background:{urgency_color};color:#ffffff;font-size:12px;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:0.04em;padding:4px 10px;border-radius:999px;">{urgency}</span>'
    ]
    if lead_temperature in ("hot", "very_hot"):
        badges.append(
            f'<span style="display:inline-block;background:{_TEMPERATURE_COLORS["hot"]};color:#ffffff;font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:0.04em;padding:4px 10px;border-radius:999px;'
            f'margin-left:6px;">\U0001F525 Hot Lead</span>'
        )
    badges_html = "\n".join(badges)

    rows = [f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Property</td>'
             f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;font-weight:600;">{property_name}</td></tr>',
             f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Reason</td>'
             f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{reason}</td></tr>']
    if call_summary:
        rows.append(
            f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;vertical-align:top;">Summary</td>'
            f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{call_summary}</td></tr>'
        )
    if guest_phone:
        rows.append(
            f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Guest</td>'
            f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{guest_phone}</td></tr>'
        )
    rows_html = "\n".join(rows)

    buttons = [
        f'<a href="{dashboard_url}" style="display:inline-block;background:{_PRIMARY};color:#ffffff;'
        f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
        f'margin:4px 8px 4px 0;">Open Dashboard</a>'
    ]
    if whatsapp_url:
        buttons.append(
            f'<a href="{whatsapp_url}" style="display:inline-block;background:{_CARD};color:{_FOREGROUND};'
            f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
            f'border:1px solid {_BORDER};margin:4px 0;">Message Guest on WhatsApp</a>'
        )
    buttons_html = "\n".join(buttons)

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:{_BACKGROUND};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BACKGROUND};padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:{_CARD};border:1px solid {_BORDER};border-radius:12px;overflow:hidden;">
<tr><td style="padding:20px 28px;border-bottom:1px solid {_BORDER};">
<span style="font-size:18px;font-weight:700;color:{_PRIMARY};">Mira</span>
</td></tr>
<tr><td style="padding:24px 28px 8px 28px;">
{badges_html}
<h1 style="font-size:20px;margin:14px 0 18px 0;color:{_FOREGROUND};">Escalation needs your attention</h1>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{rows_html}
</table>
</td></tr>
<tr><td style="padding:8px 28px 28px 28px;">
{buttons_html}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {_BORDER};">
<span style="font-size:12px;color:{_MUTED};">Sent by Mira on behalf of your property management assistant.</span>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def build_photos_email_html(
    *,
    property_name: str,
    guest_phone: str,
    gallery_url: str,
    call_page_url: str | None = None,
) -> str:
    whatsapp_url = _whatsapp_link(guest_phone)

    buttons = [
        f'<a href="{gallery_url}" style="display:inline-block;background:{_PRIMARY};color:#ffffff;'
        f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
        f'margin:4px 8px 4px 0;">View Gallery</a>'
    ]
    if whatsapp_url:
        buttons.append(
            f'<a href="{whatsapp_url}" style="display:inline-block;background:{_CARD};color:{_FOREGROUND};'
            f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
            f'border:1px solid {_BORDER};margin:4px 0;">Message Guest on WhatsApp</a>'
        )
    buttons_html = "\n".join(buttons)

    footer = (
        "Sent by Mira on behalf of your property management assistant."
        if not call_page_url
        else f'<a href="{call_page_url}" style="color:{_MUTED};">View this call</a> &middot; '
        "Sent by Mira on behalf of your property management assistant."
    )

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:{_BACKGROUND};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BACKGROUND};padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:{_CARD};border:1px solid {_BORDER};border-radius:12px;overflow:hidden;">
<tr><td style="padding:20px 28px;border-bottom:1px solid {_BORDER};">
<span style="font-size:18px;font-weight:700;color:{_PRIMARY};">Mira</span>
</td></tr>
<tr><td style="padding:24px 28px 8px 28px;">
<h1 style="font-size:20px;margin:0 0 14px 0;color:{_FOREGROUND};">A guest asked to see photos</h1>
<p style="font-size:14px;color:{_FOREGROUND};margin:0 0 4px 0;"><b>{property_name}</b></p>
<p style="font-size:14px;color:{_MUTED};margin:0 0 18px 0;">Guest: {guest_phone}</p>
</td></tr>
<tr><td style="padding:8px 28px 28px 28px;">
{buttons_html}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {_BORDER};">
<span style="font-size:12px;color:{_MUTED};">{footer}</span>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def build_call_summary_email_html(
    *,
    property_name: str,
    guest_name: str | None,
    guest_phone: str | None,
    call_type: str,
    conversation_summary: str,
    duration_minutes: float | None,
    lead_temperature: str | None,
    call_page_url: str,
) -> str:
    badges_html = ""
    if lead_temperature in ("hot", "very_hot"):
        badges_html = (
            f'<span style="display:inline-block;background:{_TEMPERATURE_COLORS["hot"]};color:#ffffff;font-size:12px;'
            f'font-weight:700;text-transform:uppercase;letter-spacing:0.04em;padding:4px 10px;border-radius:999px;'
            f'margin-bottom:10px;">\U0001F525 Hot Lead</span>'
        )

    whatsapp_url = _whatsapp_link(guest_phone) if guest_phone else None

    rows = [
        f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Property</td>'
        f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;font-weight:600;">{property_name}</td></tr>',
        f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Call type</td>'
        f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{call_type}</td></tr>',
    ]
    if guest_name:
        rows.append(
            f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Guest</td>'
            f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{guest_name}</td></tr>'
        )
    if guest_phone:
        rows.append(
            f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Phone</td>'
            f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{guest_phone}</td></tr>'
        )
    if duration_minutes is not None:
        rows.append(
            f'<tr><td style="padding:4px 0;color:{_MUTED};font-size:14px;">Duration</td>'
            f'<td style="padding:4px 0;color:{_FOREGROUND};font-size:14px;">{duration_minutes} min</td></tr>'
        )
    rows_html = "\n".join(rows)

    buttons = [
        f'<a href="{call_page_url}" style="display:inline-block;background:{_PRIMARY};color:#ffffff;'
        f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
        f'margin:4px 8px 4px 0;">Open Call Details</a>'
    ]
    if whatsapp_url:
        buttons.append(
            f'<a href="{whatsapp_url}" style="display:inline-block;background:{_CARD};color:{_FOREGROUND};'
            f'text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;'
            f'border:1px solid {_BORDER};margin:4px 0;">Message Guest on WhatsApp</a>'
        )
    buttons_html = "\n".join(buttons)

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:{_BACKGROUND};font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_BACKGROUND};padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:{_CARD};border:1px solid {_BORDER};border-radius:12px;overflow:hidden;">
<tr><td style="padding:20px 28px;border-bottom:1px solid {_BORDER};">
<span style="font-size:18px;font-weight:700;color:{_PRIMARY};">Mira</span>
</td></tr>
<tr><td style="padding:24px 28px 8px 28px;">
{badges_html}
<h1 style="font-size:20px;margin:14px 0 18px 0;color:{_FOREGROUND};">Call summary</h1>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
{rows_html}
</table>
<p style="font-size:14px;color:{_FOREGROUND};margin:18px 0 0 0;line-height:1.5;">{conversation_summary}</p>
</td></tr>
<tr><td style="padding:8px 28px 28px 28px;">
{buttons_html}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {_BORDER};">
<span style="font-size:12px;color:{_MUTED};">Sent by Mira on behalf of your property management assistant.</span>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def build_admin_login_code_email_html(code: str, ttl_minutes: int) -> str:
    """One-time login code for the internal /admin panel (app/api/v1/admin_auth.py)."""
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:{_BACKGROUND};font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_FOREGROUND};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;margin:0 auto;background:{_CARD};border:1px solid {_BORDER};border-radius:14px;">
    <tr><td style="padding:28px 28px 8px 28px;">
      <div style="font-size:20px;"><span style="color:{_PRIMARY};">&#10035;</span> mira <span style="color:{_MUTED};font-size:13px;">admin</span></div>
    </td></tr>
    <tr><td style="padding:8px 28px 4px 28px;font-size:15px;">Your admin sign-in code:</td></tr>
    <tr><td style="padding:8px 28px 8px 28px;">
      <div style="font-size:32px;font-weight:600;letter-spacing:8px;font-family:Menlo,Consolas,monospace;">{code}</div>
    </td></tr>
    <tr><td style="padding:4px 28px 28px 28px;font-size:13px;color:{_MUTED};">
      Expires in {ttl_minutes} minutes and works once. If you didn't request it, ignore this email.
    </td></tr>
  </table>
</body></html>"""


# --------------------------------------------------------------------------
# Service health alerts + daily digest (app/services/health_monitor_service.py)
# --------------------------------------------------------------------------

_STATE_COLORS = {
    "down": ("#b8452f", "#f6dfda", "Down"),
    "degraded": ("#a87a1f", "#f7ecd6", "Degraded"),
    "up": ("#5d7049", "#e6ecdf", "Up"),
    "unknown": ("#6b7280", "#eceae6", "No data"),
    "not_configured": ("#6b7280", "#eceae6", "Not configured"),
}


def _esc(value) -> str:
    import html

    return html.escape(str(value)) if value is not None else ""


_CREDIT_LEVELS = {"ok": ("up", "Healthy"), "low": ("degraded", "Low"), "critical": ("down", "Recharge")}


def _state_pill(state: str, label: str | None = None) -> str:
    color, bg, default_label = _STATE_COLORS.get(state, _STATE_COLORS["unknown"])
    label = label or default_label
    return (
        f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;'
        f'color:{color};background:{bg};">{label}</span>'
    )


def _health_shell(heading: str, body_rows: str, *, admin_url: str, accent: str = _PRIMARY) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:{_BACKGROUND};font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:{_FOREGROUND};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;background:{_CARD};border:1px solid {_BORDER};border-top:4px solid {accent};border-radius:14px;">
    <tr><td style="padding:24px 28px 4px 28px;">
      <div style="font-size:18px;"><span style="color:{_PRIMARY};">&#10035;</span> mira <span style="color:{_MUTED};font-size:13px;">system health · {_esc(settings.environment)}</span></div>
    </td></tr>
    <tr><td style="padding:12px 28px 4px 28px;font-size:20px;font-weight:600;line-height:1.35;">{heading}</td></tr>
    {body_rows}
    <tr><td style="padding:20px 28px 28px 28px;">
      <a href="{_esc(admin_url)}" style="display:inline-block;background:{_FOREGROUND};color:#ffffff;text-decoration:none;padding:10px 18px;border-radius:8px;font-size:14px;font-weight:600;">Open admin panel</a>
    </td></tr>
  </table>
</body></html>"""


def _kv_rows(pairs: list[tuple[str, str]]) -> str:
    rows = "".join(
        f'<tr><td style="padding:6px 0;color:{_MUTED};font-size:13px;width:150px;vertical-align:top;">{_esc(k)}</td>'
        f'<td style="padding:6px 0;font-size:14px;vertical-align:top;word-break:break-word;">{v}</td></tr>'
        for k, v in pairs
        if v
    )
    return f'<tr><td style="padding:8px 28px 0 28px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'


def build_health_alert_email_html(
    *,
    kind: str,
    label: str,
    state: str,
    description: str,
    critical: bool,
    opened_at: str,
    duration: str | None,
    first_error: str | None,
    last_error: str | None,
    error_count: int,
    sample_call_session_id: str | None,
    admin_url: str,
    is_credits: bool = False,
) -> str:
    """kind: down | degraded | reminder | resolved."""
    if is_credits:
        headings = {
            "down": f"{_esc(label)} credits are critically low",
            "degraded": f"{_esc(label)} credits are running low",
            "reminder": f"{_esc(label)} credits are still critically low",
            "resolved": f"{_esc(label)} credits are healthy again",
        }
    else:
        headings = {
            "down": f"{_esc(label)} is down",
            "degraded": f"{_esc(label)} is degraded",
            "reminder": f"{_esc(label)} is still down",
            "resolved": f"{_esc(label)} has recovered",
        }
    accent = {"down": "#b8452f", "reminder": "#b8452f", "degraded": "#a87a1f", "resolved": "#5d7049"}[kind]
    if is_credits and kind in ("down", "reminder"):
        impact = "Recharge now -- calls/messages using this account fail once it runs out."
    elif critical and kind in ("down", "reminder"):
        impact = "This service is on the live-call path: guests calling right now may be affected."
    else:
        impact = None
    code_style = f"font-family:Menlo,Consolas,monospace;font-size:12px;background:{_BACKGROUND};padding:8px 10px;border-radius:6px;display:block;"
    rows = _kv_rows(
        [
            ("Status", _state_pill(*_CREDIT_LEVELS[{"up": "ok", "degraded": "low", "down": "critical"}[state]]) if is_credits and state in ("up", "degraded", "down") else _state_pill(state)),
            ("Impact", f'<strong style="color:#b8452f;">{impact}</strong>' if impact else ""),
            ("What it is", _esc(description)),
            ("Started", _esc(opened_at)),
            ("Duration", _esc(duration) if duration else ""),
            ("Failures seen", _esc(error_count) if error_count and not is_credits else ""),
            ("Balance" if is_credits else "First error", f'<code style="{code_style}">{_esc(first_error)}</code>' if first_error else ""),
            (
                "Latest error",
                f'<code style="{code_style}">{_esc(last_error)}</code>' if last_error and last_error != first_error else "",
            ),
            (
                "Example call",
                f'<code style="{code_style}">{_esc(sample_call_session_id)}</code><span style="font-size:12px;color:{_MUTED};">Railway logs: @call_session_id:{_esc(sample_call_session_id)}</span>'
                if sample_call_session_id
                else "",
            ),
        ]
    )
    return _health_shell(headings[kind], rows, admin_url=admin_url, accent=accent)


def build_health_digest_email_html(
    *,
    overall: str,
    services: list[dict],
    incidents: list[dict],
    credits: list[dict],
    admin_url: str,
    date_label: str,
) -> str:
    """services: [{label, group_label, state, availability, errors_24h, note}]
    incidents: [{label, severity, opened_at, duration, resolved, first_error}]
    credits: [{label, remaining, used_of, days_left, level}]"""
    th = f'style="text-align:left;font-size:12px;color:{_MUTED};font-weight:600;padding:6px 8px;border-bottom:1px solid {_BORDER};"'
    td = f'style="font-size:13px;padding:7px 8px;border-bottom:1px solid {_BORDER};vertical-align:top;"'

    def table(headers: list[str], rows: list[list[str]]) -> str:
        head = "".join(f"<th {th}>{_esc(h)}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td {td}>{cell}</td>" for cell in row) + "</tr>" for row in rows)
        return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{head}</tr>{body}</table>'

    def section(title: str, inner: str) -> str:
        return (
            f'<tr><td style="padding:18px 28px 0 28px;"><div style="font-size:14px;font-weight:600;margin-bottom:6px;">{_esc(title)}</div>'
            f"{inner}</td></tr>"
        )

    credit_rows = [
        [
            _esc(c["label"]),
            _state_pill(*_CREDIT_LEVELS.get(c.get("level") or "", ("unknown", "—"))),
            _esc(c.get("remaining") or "—"),
            _esc(c.get("days_left") or "—"),
        ]
        for c in credits
    ]
    incident_rows = [
        [
            _esc(i["label"]),
            _state_pill(i["severity"]),
            _esc(i["opened_at"]),
            _esc(i["duration"]) + ("" if i["resolved"] else " (ongoing)"),
            f'<span style="font-size:12px;color:{_MUTED};">{_esc((i.get("first_error") or "")[:160])}</span>',
        ]
        for i in incidents
    ]
    service_rows = [
        [
            _esc(s["label"]) + f'<div style="font-size:11px;color:{_MUTED};">{_esc(s["group_label"])}</div>',
            _state_pill(s["state"]),
            _esc(s["availability"]),
            _esc(s["incidents_24h"]),
            f'<span style="font-size:12px;color:{_MUTED};">{_esc(s.get("note") or "")}</span>',
        ]
        for s in services
    ]
    heading = {
        "up": "All systems operational",
        "degraded": "Some services are degraded",
        "down": "One or more services are down",
    }.get(overall, "Daily health report")
    body = (
        f'<tr><td style="padding:4px 28px 0 28px;font-size:13px;color:{_MUTED};">{_esc(date_label)} · last 24 hours</td></tr>'
        + section("Credits left", table(["Account", "Level", "Remaining", "Days left"], credit_rows) if credit_rows else "<div style='font-size:13px;color:#635747;'>No balances configured yet.</div>")
        + section(
            f"Incidents ({len(incidents)})",
            table(["Service", "Worst", "Started", "Duration", "Error"], incident_rows)
            if incident_rows
            else f'<div style="font-size:13px;color:{_MUTED};">No incidents in the last 24 hours.</div>',
        )
        + section("Services", table(["Service", "Now", "Availability", "Incidents", "Note"], service_rows))
    )
    accent = {"up": "#5d7049", "degraded": "#a87a1f", "down": "#b8452f"}.get(overall, _PRIMARY)
    return _health_shell(heading, body, admin_url=admin_url, accent=accent)

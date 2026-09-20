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
    if lead_temperature == "hot":
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
    if lead_temperature == "hot":
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

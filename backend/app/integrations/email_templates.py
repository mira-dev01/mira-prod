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


def build_escalation_email_html(
    *,
    property_name: str,
    urgency: str,
    reason: str,
    call_summary: str | None,
    guest_phone: str | None,
) -> str:
    urgency_color = _URGENCY_COLORS.get(urgency, "#6b7280")
    dashboard_url = f"{settings.frontend_base_url}/dashboard/leads"
    whatsapp_url = _whatsapp_link(guest_phone) if guest_phone else None

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
<span style="display:inline-block;background:{urgency_color};color:#ffffff;font-size:12px;font-weight:700;
text-transform:uppercase;letter-spacing:0.04em;padding:4px 10px;border-radius:999px;">{urgency}</span>
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


def build_photos_email_html(*, property_name: str, guest_phone: str, gallery_url: str) -> str:
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
<a href="{gallery_url}" style="display:inline-block;background:{_PRIMARY};color:#ffffff;
text-decoration:none;font-size:14px;font-weight:600;padding:12px 20px;border-radius:8px;">View Gallery</a>
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {_BORDER};">
<span style="font-size:12px;color:{_MUTED};">This is the link send_photos will hand guests directly over
WhatsApp once that channel is live -- routed to your inbox for now so the flow is testable.</span>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

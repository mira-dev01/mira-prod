"""Builds the per-call system prompt injected into the transient Vapi
assistant config. FAQ/house-rules/local-tips are inlined directly here
rather than retrieved from a vector DB -- at Tier 1 call volume for a
handful of properties, a RAG/Pinecone pipeline is unnecessary complexity
(genuinely Tier 2/3 scope per the spec).
"""

from app.models.guest_profile import GuestProfile
from app.models.property import Property

BASE_INSTRUCTIONS = """You are Mira, a warm, efficient AI voice receptionist for an Airbnb host in India.
You answer guest calls 24/7. Speak naturally, keep responses brief (this is a phone call, not a chat).
Always confirm dates and the number of guests before calling a tool. Use the property_id given to you
below for every tool call -- never ask the guest for it.

Capabilities:
- Check availability and quote pricing using your tools, do not guess numbers.
- Answer FAQs using the house rules and amenities provided below.
- If the guest reports an urgent issue (no water, no AC, lockout, safety concern), use escalate_to_host
  or dispatch_technician as appropriate -- do not try to resolve physical issues yourself.
- If you cannot help or the guest asks for a human, use escalate_to_host with urgency reflecting how
  time-sensitive it is.
- For WhatsApp confirmations the guest asks for, use send_whatsapp.
"""


def build_system_prompt(property_: Property, guest: GuestProfile | None) -> str:
    sections = [BASE_INSTRUCTIONS]

    sections.append(
        f"\nCurrent property:\n"
        f"- property_id: {property_.id}\n"
        f"- name: {property_.name}\n"
        f"- city: {property_.city or 'unknown'}\n"
        f"- check-in time: {property_.check_in_time}, check-out time: {property_.check_out_time}\n"
        f"- max guests: {property_.max_guests}\n"
        f"- base nightly rate: ₹{float(property_.base_price):,.0f}"
    )

    if property_.house_rules:
        sections.append(f"\nHouse rules:\n{property_.house_rules}")

    if property_.amenities:
        sections.append(f"\nAmenities: {', '.join(property_.amenities)}")

    if property_.faq:
        faq_lines = "\n".join(f"Q: {item['question']}\nA: {item['answer']}" for item in property_.faq)
        sections.append(f"\nFrequently asked questions:\n{faq_lines}")

    if guest is not None:
        sections.append(
            f"\nThis caller is a returning guest: {guest.name or 'name unknown'}, "
            f"{guest.total_stays} past stay(s). Greet them personally and use this history "
            f"to inform your tone (e.g. loyalty tier for negotiate_rate)."
        )
    else:
        sections.append("\nThis caller is not in our guest records -- treat them as a new guest.")

    return "\n".join(sections)


def first_message_for(property_: Property, guest: GuestProfile | None) -> str:
    if guest is not None and guest.name:
        return f"Namaste {guest.name}! I'm Mira, your virtual assistant for {property_.name}. How can I help you today?"
    return f"Namaste! I'm Mira, your virtual assistant for {property_.name}. How can I help you today?"

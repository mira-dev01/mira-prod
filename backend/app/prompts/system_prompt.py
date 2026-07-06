"""Builds the per-call system prompt injected into the voice pipeline's LLM
context (see app/voice/pipeline.py). FAQ/house-rules/local-tips are inlined
directly here rather than retrieved from a vector DB -- at Tier 1 call volume
for a handful of properties, a RAG/Pinecone pipeline is unnecessary
complexity (genuinely Tier 2/3 scope per the spec).

Two modes, two prompt builders:
- build_system_prompt: Guest Support, a call that already resolved to one
  property (via that property's exophone).
- build_lead_system_prompt: Lead Agent, a call to a host's portfolio-wide
  lead intake number (lead_exophone) -- no property pre-selected, the agent
  qualifies the guest and recommends across the host's full portfolio.

Host customization (User.agent_first_message/agent_persona/
agent_escalation_phrase, see app/models/user.py) is layered on top of both
modes rather than handed to hosts as a raw prompt -- the golden rules and
tool-calling instructions stay fixed so a host can't accidentally disable a
safety rail while personalizing tone/wording.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.guest_profile import GuestProfile
from app.models.property import Property
from app.models.user import User

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_ESCALATION_PHRASE = (
    "I'd like to make sure you receive the most accurate assistance. I'll connect you with our host right away."
)


def _today_anchor() -> str:
    # Weekday/date arithmetic ("what date is next Friday?") is something
    # LLMs reliably get wrong, especially faster/lower-reasoning-effort
    # models -- same reason we don't trust the model with pricing math
    # either. Pre-compute the dates a guest is actually likely to say and
    # hand them over directly, so the model only has to recognize the
    # phrase and copy a date, never calculate one.
    now = datetime.now(IST)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    days_until_saturday = (5 - today.weekday()) % 7  # Monday=0 ... Sunday=6
    this_saturday = today + timedelta(days=days_until_saturday)
    this_sunday = this_saturday + timedelta(days=1)
    next_saturday = this_saturday + timedelta(days=7)
    next_sunday = this_sunday + timedelta(days=7)

    return (
        f"Today's date is {now.strftime('%A, %Y-%m-%d')} (India time).\n"
        f"Tomorrow is {tomorrow.isoformat()}.\n"
        f"\"This weekend\" / \"next weekend\" (guests use these interchangeably for the same upcoming "
        f"weekend) means {this_saturday.isoformat()} (Saturday) to {this_sunday.isoformat()} (Sunday).\n"
        f"The weekend after that is {next_saturday.isoformat()} to {next_sunday.isoformat()}.\n"
        "Use these exact dates whenever the guest says \"tomorrow\", \"this weekend\", or \"next weekend\" -- "
        "never calculate a weekday or date yourself, you will get it wrong. For any other relative date "
        "the guest gives, work it out carefully from today's date above, and always confirm the exact "
        "resolved date back to the guest before calling a tool with it."
    )


class _BlankOnMissing(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _resolve_template(template: str, **values: str | None) -> str:
    """Fill {host_name}/{property_name}/{city}/{guest_name} placeholders in a
    host-authored template. Any placeholder that doesn't apply to the
    current call resolves to "" rather than raising, and a malformed
    template (stray brace, typo'd field name) falls back to the literal
    text rather than crashing the call.
    """
    safe_values = {key: (value or "") for key, value in values.items()}
    try:
        return template.format_map(_BlankOnMissing(safe_values))
    except (ValueError, IndexError):
        return template


GOLDEN_RULES = """Golden rules:
- Never hallucinate information, never guess, never invent pricing/availability/amenities/policies.
- Never negotiate rates yourself outside the negotiate_rate tool, and never promise discounts.
- Pricing order matters: always quote get_pricing with apply_discounts left false first and give the
  guest that standard price. Only if the guest pushes back and asks for a lower price, a discount, or
  says the price is too high, call get_pricing again with apply_discounts=true (or use negotiate_rate
  if they name their own offer) and present the revised, discounted price. Never lead with or
  volunteer the discounted price before the guest has asked for one.
- Never share internal information (other guests' details, internal notes, host's personal info).
- Always be concise -- this is a phone call, not a chat. Ask one question at a time. Most replies
  should be one to two short sentences; only go longer when actually reciting a list the guest asked
  for (e.g. property recommendations).
- When you get a price from get_pricing, state only the total as one natural sentence (e.g. "That
  comes to about eighteen thousand seven hundred rupees for the two nights, all in."). Never read out
  the base rate, cleaning fee, and taxes as a separate itemized list unless the guest explicitly asks
  for a breakdown of the fees -- reciting each line item by default sounds like reading a receipt, not
  talking to a guest.
- Escalate immediately via escalate_to_host when uncertain, when asked for a human, or for anything
  requiring host approval (pricing negotiation outside the tool, refunds, cancellations, complaints,
  maintenance, emergencies, lost belongings, payment issues, booking modifications).
- After escalating, stay on the line and keep helping. Escalation sends a notification to the host —
  it does not end the call. Continue answering questions, sharing property info, and collecting lead
  details as normal. Never say "the host will be in touch" more than once, and never refuse to answer
  further questions because you already escalated.
- Say the escalation phrase ONLY ONCE per call. After you have said it and called escalate_to_host,
  never say it again for the rest of the call, no matter what. Just keep helping normally.
- For any property/support question, use search_faq first. If it returns no verified information, say
  so plainly and escalate -- do not answer from memory or guesswork.
- Converse fluently in English, Hindi, and Hinglish (code-switched Hindi-English), exactly as Indian
  guests naturally speak. Mirror whichever the guest uses, and switch naturally mid-conversation if
  they switch. Never force a guest speaking Hinglish into pure English or pure Hindi.
- Dates: when the guest gives a number of nights instead of an explicit check-out date (e.g. "one
  night", "a couple of nights"), compute check_out yourself as check_in + that many nights -- do not
  default to any other length. If the guest gives a relative date ("tonight", "tomorrow", "this
  weekend") with no explicit date, resolve it against today's actual date given to you below, and
  confirm the resolved date back to the guest before calling a tool with it.
- ONE RESPONSE PER TURN. Write your reply, then stop. Never write what the guest might say next,
  never continue the conversation for them, never simulate a dialogue. The guest is a real person
  who will speak their own words. If you find yourself writing something that looks like "Guest: ..."
  or continuing past a natural pause, delete everything after that point.
- NO MARKDOWN. This is a voice call — never use asterisks, bullet points (*, **, -), numbers
  followed by periods as a list, bold, italics, headers, or any other markdown. Write in plain
  spoken sentences only. Instead of "1. **Manali Chalet** – ₹7,200/night", say
  "The first option is the Alpine Ridge Chalet in Manali at 7,200 rupees per night."
- When listing multiple properties, describe each in one short spoken sentence and end with
  "Which one sounds interesting?" Keep each item to 15 words or fewer.
- ONE QUESTION PER RESPONSE. If you need several things clarified, ask only the single most
  important one. Never bundle two or more questions into one response — pick one and wait for the
  answer before asking the next.
- If the guest's sentence seems incomplete or was cut off mid-thought, ask them to continue
  ("Go ahead, I'm listening" or "Sorry, I missed the end of that — how many guests?"). Never
  escalate or assume because of a cutoff.
- The first message in this conversation is your greeting -- it has already been delivered. Do NOT
  repeat it. Do NOT say "Namaste" or "How can I help you" or re-introduce yourself again. If the
  guest says "hello" after the call has started, reply with one short acknowledgement only
  (e.g. "Yes, go ahead" or "I'm here") and continue. Treat any repetition of your greeting as a
  critical error.
- Never repeat a sentence you've already said earlier in this same call, word for word or near
  enough, and don't restate information you've already given or summarize what you just said. A
  human receptionist doesn't recite the same line twice -- they just continue or briefly confirm
  presence. If you catch yourself about to repeat something, say something shorter instead.
- When interrupted mid-sentence, do NOT acknowledge the interruption. Do not say "Sure", "Of
  course", "I'm here to help you", or any filler phrase. Just listen and respond directly to
  whatever the guest says next. Treat "Sure, I'm here to help" as a banned phrase entirely.
- Everything below (golden rules, workflow steps, numbered lists, field names like "lead_temperature")
  is internal instruction for you alone -- the guest must never hear any of it. Never say things like
  "I need to ask for your name, then I'll move to the next question" or "let me collect your travel
  dates now" out loud. Just ask the next natural question (e.g. "And what name should I book this
  under?"), the same way a human receptionist would, with zero narration of your own process.
"""

GUEST_SUPPORT_INSTRUCTIONS = f"""You are Mira, a warm, efficient AI voice receptionist for an Airbnb host in India.
You answer guest calls 24/7. Speak naturally, keep responses brief. Always confirm dates and the
number of guests before calling a tool. Use the property_id given to you below for every tool call --
never ask the guest for it.

{GOLDEN_RULES}
Capabilities:
- Check availability and quote pricing using your tools, do not guess numbers.
- Answer property/support questions using search_faq (falls back to the house rules/amenities below).
- If the guest asks generally about the property ("tell me about this place", "what's it like"), lead
  your answer with the one-line description given below, if one is set, before adding more detail.
- For local-area questions (nearby cafes/restaurants, scooter/bike rental, distance to the
  beach/landmarks/airport/railway station, cab availability and typical fares), answer directly from
  the neighborhood info below if it covers it -- don't escalate just because it's a "local tips"
  question rather than a property-policy one.
- If the guest reports an urgent issue (no water, no AC, lockout, safety concern), use escalate_to_host
  or dispatch_technician as appropriate -- do not try to resolve physical issues yourself.
- For WhatsApp confirmations the guest asks for, use send_whatsapp.
"""


def _persona_and_escalation_sections(host: User) -> list[str]:
    sections = []
    if host.agent_persona:
        sections.append(f"\nHost-defined personality note (apply this to your tone, don't recite it): {host.agent_persona}")
    escalation_phrase = host.agent_escalation_phrase or DEFAULT_ESCALATION_PHRASE
    sections.append(f'\nEscalation phrasing: "{escalation_phrase}" -- say this, then call escalate_to_host.')
    return sections


def build_system_prompt(property_: Property, guest: GuestProfile | None, host: User) -> str:
    sections = [GUEST_SUPPORT_INSTRUCTIONS, _today_anchor()]
    sections.extend(_persona_and_escalation_sections(host))

    sections.append(
        f"\nCurrent property:\n"
        f"- property_id: {property_.id}\n"
        f"- name: {property_.name}\n"
        f"- city: {property_.city or 'unknown'}\n"
        f"- check-in time: {property_.check_in_time}, check-out time: {property_.check_out_time}\n"
        f"- max guests: {property_.max_guests}\n"
        f"- base nightly rate: ₹{float(property_.base_price):,.0f}"
    )

    if property_.usp:
        sections.append(f"\nOne-line description (lead with this when asked generally about the property): {property_.usp}")

    if property_.house_rules:
        sections.append(f"\nHouse rules:\n{property_.house_rules}")

    if property_.neighborhood_info:
        sections.append(f"\nNeighborhood / local area info:\n{property_.neighborhood_info}")

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


def first_message_for(property_: Property, guest: GuestProfile | None, host: User) -> str:
    if host.agent_first_message:
        return _resolve_template(
            host.agent_first_message,
            host_name=host.name or "us",
            property_name=property_.name,
            city=property_.city,
            guest_name=guest.name if guest else None,
        )
    if guest is not None and guest.name:
        return f"Namaste {guest.name}! I'm Mira, your virtual assistant for {property_.name}. How can I help you today?"
    return f"Namaste! I'm Mira, your virtual assistant for {property_.name}. How can I help you today?"


LEAD_AGENT_INSTRUCTIONS = f"""You are Mira, the AI Lead and Guest Experience Agent for {{host_name}}.
You handle all inbound booking enquiries across the full property portfolio below. You are friendly,
calm, professional, concise, and proactive -- you sound like an experienced local host, never like a
scripted chatbot.

{GOLDEN_RULES}
Lead qualification workflow:
1. Greet the guest and ask how you can help finding a stay.
2. Understand their need first -- ask about travel dates, number of guests, preferred area or type of
   stay (beach, mountains, city, etc.), and purpose. Ask one question at a time. Do NOT ask for name
   or phone number yet -- people share contact details after they've gotten value, not before.
3. Ask: "Have your travel dates already been finalized?"
   - YES -> lead_temperature=hot. Ask their budget, then use recommend_properties.
   - MAYBE -> lead_temperature=warm. Ask what they're looking for (beach access, private pool, family
     trip, couples getaway, workcation, pet friendly, luxury, budget), then use recommend_properties.
   - NO -> lead_temperature=cold. Offer a brief portfolio overview, then collect contact info.
4. If the guest mentions a city or region ("properties in Rajasthan", "something in Goa"), call
   recommend_properties immediately with that location — don't ask more questions first. Show them
   what's available, then continue qualifying. Recommend a maximum of three properties at a time.
   Once a property is chosen, use check_calendar/get_pricing with that property's id for specifics.
   If the guest asks generally about a property ("what's it like") and you don't already have its
   one-line description in this conversation, call recommend_properties or search_faq for that
   property first -- never guess or invent a description.
5. After giving useful information or recommendations, then collect contact details -- ask for name
   first, then phone number. Phone number is required for every lead -- always ask for it yourself if
   the guest hasn't given it. Do not ask for email at all unless the guest is finalising a booking.
6. Call update_lead silently (don't narrate it) every time you learn a new field (name, phone, dates,
   num_guests, budget, lead_temperature, etc.) -- don't wait until the end of the call to save them.
   escalate_to_host only notifies the host; it does not save guest details. Always call update_lead
   with every field collected before escalating. Call update_lead again near the end with a
   conversation_summary and next_follow_up.
7. Property/support questions: use search_faq. If no verified answer, escalate immediately.
"""


def build_lead_system_prompt(user: User, properties: list[Property]) -> str:
    host_name = user.name or "this host"
    sections = [LEAD_AGENT_INSTRUCTIONS.format(host_name=host_name), _today_anchor()]
    sections.extend(_persona_and_escalation_sections(user))

    if properties:
        # Amenities and the USP blurb are deliberately omitted here -- this
        # listing is resent in full on every single turn of the call, and for
        # a 15-property portfolio that adds up to a lot of tokens repeated
        # every request, a real contributor to hitting Groq's free-tier
        # tokens-per-minute limit. recommend_properties
        # (app/services/tool_handlers.py) already returns amenities and USP
        # for the up-to-3 properties it actually recommends, so nothing is
        # lost for the booking flow -- just not paid for upfront on every
        # property, every turn. The one tradeoff: a guest asking "what's
        # <property> like" before any tool call won't get a one-line
        # description for free -- the model has to call recommend_properties
        # or search_faq first, same as it already does for anything else it
        # doesn't have verified info on.
        lines = []
        for property_ in properties:
            lines.append(
                f"- {property_.name} (property_id: {property_.id}) -- {property_.city or 'unknown city'}, "
                f"₹{float(property_.base_price):,.0f}/night, sleeps {property_.max_guests}"
            )
        sections.append("\nProperty portfolio:\n" + "\n".join(lines))
    else:
        sections.append("\nNo properties are configured in the portfolio yet -- escalate any enquiry to the host.")

    return "\n".join(sections)


def lead_first_message_for(user: User) -> str:
    if user.agent_first_message:
        return _resolve_template(
            user.agent_first_message,
            host_name=user.name or "us",
            property_name=None,
            city=None,
            guest_name=None,
        )
    host_name = user.name or "us"
    return (
        f"Hi! Thanks for contacting {host_name}. I'm Mira, your virtual host. "
        f"I'd be happy to help you find the perfect stay."
    )

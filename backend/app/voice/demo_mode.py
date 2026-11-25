"""What the pipeline does differently on the marketing site's public "test the agent" call, and nothing else.

A demo call runs the same prompt, LLM, speech services and guard chain as a real call, against a sample host
(`DEMO_HOST_EMAIL`), for a stranger. So the things a real call would do to the outside world must not happen:

  * messages to people: WhatsApp texts, photos, shortlists, payment links, technician and host alerts, e-mail;
  * a live transfer of the call to a phone;
  * the post-call steps (call classification, summary LLM, host e-mail, guest memory): they cost money and
    message people, and a demo caller is neither a lead for the sample host nor a guest of it.

The agent still hears a normal tool result for each of those tools, so the conversation flows; it is told plainly
that nothing was sent, so it can say what would happen in a real setup instead of claiming it did.

Nothing here runs on a production call: the flag is only ever set inside `demo_scope()`, which only
`pipeline.run_browser_demo_pipeline` enters. A ContextVar (not an argument) so it reaches every tool and handler of
that one call, through the tasks pipecat starts for it, without threading a parameter through the pipeline.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_active: ContextVar[bool] = ContextVar("mira_demo_call_active", default=False)


def is_demo() -> bool:
    return _active.get()


@contextmanager
def demo_scope() -> Iterator[None]:
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)


# Every tool that reaches a person or a phone. A new tool that does either must be added here.
SIMULATED_TOOLS: frozenset[str] = frozenset(
    {
        "send_payment_link",
        "dispatch_technician",
        "send_whatsapp",
        "send_photos",
        "send_property_list",
        "request_early_late_checkout",
        "escalate_to_host",
        "request_host_transfer",
    }
)

_SIMULATED_RESULTS: dict[str, str] = {
    "send_payment_link": (
        "Demo call: no payment link was created or sent. Tell the guest briefly that on a real booking the "
        "link would arrive on their WhatsApp now, then carry on."
    ),
    "dispatch_technician": (
        "Demo call: no technician was contacted. Tell the guest briefly that on a real call the right person "
        "would be sent the request on WhatsApp now, then carry on."
    ),
    "send_whatsapp": (
        "Demo call: no WhatsApp message was sent. Tell the guest briefly that on a real call it would reach "
        "them on WhatsApp, then carry on."
    ),
    "send_photos": (
        "Demo call: no photos were sent. Tell the guest briefly that on a real call the photos would arrive "
        "on WhatsApp, then carry on."
    ),
    "send_property_list": (
        "Demo call: no list was sent. Tell the guest briefly that on a real call the list of stays would "
        "arrive on WhatsApp, then carry on."
    ),
    "request_early_late_checkout": (
        "Demo call: no request was sent to the host. Tell the guest briefly that on a real call the host would "
        "be asked now and would reply, then carry on."
    ),
    "escalate_to_host": (
        "Demo call: the host was not notified. Tell the guest briefly that on a real call the host would be "
        "told right away and would follow up, then carry on."
    ),
    "request_host_transfer": (
        "Demo call: no live transfer happens in this test. Tell the guest briefly that on a real call the "
        "host would be connected now, and offer to keep helping."
    ),
}


def simulated_result(tool_name: str) -> str:
    return _SIMULATED_RESULTS[tool_name]

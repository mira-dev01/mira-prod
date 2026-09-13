"use client";

import { MessageCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { whatsappLink } from "@/lib/utils";

/**
 * "Message on WhatsApp" CTA -- opens the guest's own WhatsApp chat directly
 * (wa.me deep link) so a host can take the conversation further manually.
 * Deliberately separate from this codebase's Twilio WhatsApp Business API
 * sends (app/integrations/twilio_client.py) -- this is a plain client-side
 * link a human clicks, not a template-based automated send.
 *
 * Renders nothing if there's no usable phone number (null/browser-test),
 * so callers can drop it in unconditionally rather than guarding every
 * call site with their own isBrowserTestIdentity check.
 */
export function WhatsAppButton({
  phone,
  size = "icon-sm",
  stopPropagation = false,
}: {
  phone: string | null | undefined;
  size?: "icon-sm" | "icon" | "sm";
  stopPropagation?: boolean;
}) {
  const href = whatsappLink(phone);
  if (!href) return null;

  const iconOnly = size === "icon-sm" || size === "icon";

  return (
    <Button
      variant="outline"
      size={size}
      aria-label="Message on WhatsApp"
      title="Message on WhatsApp"
      render={
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => stopPropagation && e.stopPropagation()}
        />
      }
    >
      <MessageCircle />
      {!iconOnly && "WhatsApp"}
    </Button>
  );
}

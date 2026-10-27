"""ExotelFrameSerializer that logs what Exotel sends us, without changing it.

pipecat's own serializer silently drops every non-media Exotel event
(`start`, `stop`, `mark`, ...), so when Exotel ends a stream there is no
record of why. This subclass only observes: it logs each non-media event and
a sparse inbound-media counter, then hands the message to the parent
unchanged. Nothing here alters frames, so it can never affect a live call.
"""

import json
import logging

from pipecat.serializers.exotel import ExotelFrameSerializer

logger = logging.getLogger(__name__)

# Media frames arrive ~every 20ms; log the first, then roughly every 2s.
_MEDIA_LOG_EVERY = 100


class DiagnosticExotelFrameSerializer(ExotelFrameSerializer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._inbound_media_count = 0

    async def deserialize(self, data):
        try:
            message = json.loads(data)
            event = message.get("event") if isinstance(message, dict) else None
            if event == "media":
                self._inbound_media_count += 1
                count = self._inbound_media_count
                if count == 1 or count % _MEDIA_LOG_EVERY == 0:
                    logger.info("exotel_inbound_media count=%d", count)
            else:
                logger.info(
                    "exotel_inbound_event event=%s inbound_media_so_far=%d raw=%.300s",
                    event,
                    self._inbound_media_count,
                    data if isinstance(data, str) else repr(data),
                )
        except Exception:
            logger.debug("exotel_inbound_event unparseable", exc_info=True)
        return await super().deserialize(data)

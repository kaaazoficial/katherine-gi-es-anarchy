# Katherine Nexus

Katherine Nexus is an additive natural-language layer for Katherine. The bot keeps the same name, existing command handlers, and Anarchy Apps API integration.

## Current scope

- Converts a deliberately small set of high-confidence Spanish requests into commands already supported by Katherine.
- Reuses existing command handlers, permissions, rate limits, and API calls.
- Leaves ambiguous requests and ordinary conversation untouched.
- Does not call an LLM, add a database table, persist message content, or add undocumented Anarchy Apps endpoints.
- Routes only in the developer-bot message path; community-bot voice behavior remains unchanged.

## Safety and limitations

This is deterministic intent routing, not unrestricted natural-language understanding. If a request does not match a safe pattern, users should use !ayuda or the explicit command. The feature does not imply that Katherine can send DMs, read chat history, attach media, or moderate members through unavailable API operations.

## Rollback

The feature is isolated in katherine_nexus.py and a small dispatch integration. Reverting the integration and deleting the module/tests returns the prior message-processing path without changing existing command implementations.

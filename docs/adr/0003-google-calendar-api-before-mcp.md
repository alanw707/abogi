# Google Calendar through its REST API until the MCP server is open

The agreed design has every connector be an MCP server, reached through a Foundry Toolbox. Google's remote Calendar MCP server (`calendarmcp.googleapis.com`) refuses every tool call unless the Cloud project is enrolled in the Google Workspace Developer Preview Program, and enrollment is Google's decision with no known timeline. So the calendar connector calls the generally available Calendar API v3 directly, with the owner's own OAuth sign-in and read-only scopes, and offers the model the same kind of read tools as the MCP server. The MCP connector stays in the code; switching back is one setting (`ABOGI_CALENDAR_MCP_URL` or `ABOGI_CALENDAR_TOOLBOX` instead of `ABOGI_CALENDAR_API=google`).

## Consequences

- The calendar does not go through a Foundry Toolbox, so Abogi holds the Google refresh token itself and the hosted agent needs it as a secret.
- The tools are shaped for speech (whole days in the calendar's time zone, short text) rather than mirroring the API, which is a small amount of code Abogi owns.
- The scopes cover the calendar list and events but not `calendars.get`; anything needing more must add a scope and the owner must sign in again.
- Revisit when the Calendar MCP server leaves preview or the project is enrolled.

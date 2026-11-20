# Step 2: push-to-talk voice

Done when: holding a key on Windows and asking "what's on my calendar tomorrow" gets a spoken answer from the hosted agent through Voice Live (ADR 0002), and the run is traced.

## What step 1 measured (19 Nov 2026, hosted agent v3, gpt-5-mini, reasoning low)

| Turn | Time |
|---|---|
| New conversation, no tools ("who are you") | 7.8-8.9 s |
| New conversation, calendar question | 10.7-14.6 s |
| Follow-up in the same conversation (`previous_response_id`), no tools | 2.5-4.9 s |
| Follow-up in the same conversation, calendar question | 5.9-7.0 s |
| Client's own Azure sign-in (CLI only, once per process) | 4.3 s |

Traces show the first ~5.7-7.4 s of a new conversation is spent in Foundry (`agentsv2`, `responsesapi`) before the request reaches Abogi's container: starting a hosted session. Inside the container a calendar turn is two model calls of about 2 s each plus Google calls.

`minimal` reasoning was not reliably faster and answered worse (line-broken lists, asked for approval to read, asked "which Friday" when Friday was tomorrow), so `low` stays the default.

## What step 2 measured so far (19 Nov 2026, hosted agent v5)

Warmed REPL (`abogi --agent abogi --warm --time`), two runs:

| Turn | v4 | v5 (connector `prepare()`) |
|---|---|---|
| Warm-up turn (includes the CLI's own Azure sign-in) | 12.0 s | 9.7-10.1 s |
| First calendar question | 8.2 s | 5.6-7.0 s |
| Follow-up, no tools | 2.9 s | 3.0-3.2 s |
| Follow-up calendar question | 3.6 s | 4.5-4.9 s |

v5 starts the calendar connector's first-use work (Key Vault read, Google refresh, time-zone lookup) in the background on the container's first request, so it overlaps the warm-up's model call.

## Issues

### 1. Keep one warm conversation per voice session — done (client side)
`abogi.channels.warm.WarmConversation` keeps one chained conversation, warms it at start and whenever it may have gone cold (idle > timeout minus 60 s), and `warm_in_background()` is for push-to-talk key-down so a reheat overlaps the owner speaking. Hosted session idle timeout: `HostedAgentDefinition(session_configuration=SessionConfiguration(idle_timeout_seconds=N))`, 120-14,400 s, default 900 s, fixed per agent version (learn.microsoft.com/azure/foundry/agents/how-to/manage-hosted-sessions). Abogi uses the default for now.

### 2. Voice Live in front of the hosted agent — done (spike works)
Result, 19 Nov 2026, hosted agent v5, voice en-US-Ava:DragonHDLatestNeural, text turns:

| Turn | First text | First audio | Done |
|---|---|---|---|
| Cold first turn, calendar | 10.2 s | 10.4 s | 13.2 s |
| Silent warm-up turn (`response.create` with `modalities: ["text"]`, no audio) | 8.0 s | none | 8.0 s |
| Calendar question after warm-up | 4.7-5.7 s | 4.9-6.0 s | 7.1-8.9 s |
| Follow-up, no tools | 2.6-3.1 s | 2.7-3.2 s | 3.2-3.5 s |

- Voice Live keeps one agent conversation for the whole connection: every turn reported the same `conversation_id`, and "what did I ask you first?" was answered correctly. So the tray client keeps one Voice Live connection open and warms it silently; `WarmConversation` stays for the terminal.
- Audio starts ~0.1-0.3 s after the first text, but the agent returns text whole, so first audio waits for the full answer. Calendar turns are still ~5 s: two model calls in the container plus Google.
- Saturday's answer used 24-hour times ("16:00-16:30"): a case for issue 4.

Original notes:
Spike: `scripts/voice_spike.py` (text turns over one Voice Live connection, no microphone; reports time to first text, first audio, done). Facts from current docs and the SDK source:
- `azure-ai-voicelive` 1.3.1 is GA (api-version `2026-07-15`); installed as the `voice` extra.
- `connect(endpoint=<resource URL, not project URL>, agent_name=, project_name=, conversation_id=)`. The docs' `agent_config={...}` is silently ignored by the SDK: use the flat keywords.
- Hosted agents on the Responses protocol are supported. `instructions` is not allowed in agent mode. Passing `conversation_id` continues an earlier conversation, which is how Voice Live meets issue 1.
- Push-to-talk: `turn_detection: null`, then `input_audio_buffer.append` / `commit` / `response.create`; barge-in is `response.cancel`.
- Auth: Owner alone gets 401 (no data actions). The owner now has `Cognitive Services User` and `Foundry User` on the Foundry resource (granted 19 Nov 2026); they took about a minute to apply.
- `conversation.item.create` needs a typed `UserMessageItem`: a plain dict becomes the base item class and rejects `role`.

### 3. Windows tray client, push-to-talk — ready-for-agent
Python tray app (Windows, not WSL): hold a hotkey to talk, release to send; plays the reply; interrupt by pressing again. Signs in once with Azure at start and keeps the token (removes the 4.3 s CLI cost per question).

### 4. Spoken-length answers — needs-triage
First real voice session (20 Nov 2026, tray client) found: Abogi read tool names aloud ("list_calendars, list_events, get_event") when asked what it can do; it claimed to be "GPT-4 family with knowledge to June 2024" (it runs gpt-5-mini and should not guess); calendar times mixed "6:45–7:45" with "2:35–2:45 PM" and earlier "16:00". Fixed in hosted agent v6: the instructions forbid saying tool names and require 12-hour times, `with_model()` states the deployment so the model does not guess, and the calendar tool now returns "2:35 PM" itself. Checked through Voice Live: plain-words abilities, "gpt-5-mini model deployment in Microsoft Foundry", and every time with AM/PM. Still worth adding evaluation cases.
Answers already avoid markdown, but `minimal` showed how easily lists come back line-broken. Add a few evaluation cases that check calendar answers read naturally aloud (times as "8:10", no bullets) before tuning further.

### 5. Trace the calendar calls — needs-triage
Google Calendar HTTP calls are not in the trace yet; add spans so a slow turn shows whether Google or the model was slow.

### 6. Reply-time budget — needs-info
Decide what "fast enough" is for push-to-talk (for example, first audio within 3 s of release on a warm conversation). Voice Live can stream audio as text arrives, so the agent should stream its answer instead of returning it whole (`TextResponse` supports streaming).

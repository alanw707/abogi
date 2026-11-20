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

## Issues

### 1. Keep one warm conversation per voice session — ready-for-agent
The tray client keeps a single conversation (chain `previous_response_id`, or a Foundry conversation) for as long as it runs, and sends a silent warm-up turn at start and after long idle, so a spoken question never pays the ~5 s session start. Check `HostedAgentDefinition.session_configuration.idle_timeout_seconds` and pick an idle timeout that matches how the owner uses it.

### 2. Voice Live in front of the hosted agent — ready-for-agent
Spike the `azure-ai-voicelive` client with `agent_config={agent_name, project_name}` (pinned beta in the docs; sample under `foundry-samples/.../bring-your-own/voicelive/`). Confirm: does Voice Live reuse one agent conversation across turns (issue 1), and which voice and language settings it takes. Verify against current docs, not memory.

### 3. Windows tray client, push-to-talk — ready-for-agent
Python tray app (Windows, not WSL): hold a hotkey to talk, release to send; plays the reply; interrupt by pressing again. Signs in once with Azure at start and keeps the token (removes the 4.3 s CLI cost per question).

### 4. Spoken-length answers — needs-triage
Answers already avoid markdown, but `minimal` showed how easily lists come back line-broken. Add a few evaluation cases that check calendar answers read naturally aloud (times as "8:10", no bullets) before tuning further.

### 5. Trace the calendar calls — needs-triage
Google Calendar HTTP calls are not in the trace yet; add spans so a slow turn shows whether Google or the model was slow.

### 6. Reply-time budget — needs-info
Decide what "fast enough" is for push-to-talk (for example, first audio within 3 s of release on a warm conversation). Voice Live can stream audio as text arrives, so the agent should stream its answer instead of returning it whole (`TextResponse` supports streaming).

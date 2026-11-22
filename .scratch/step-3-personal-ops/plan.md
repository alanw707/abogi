# Step 3: personal ops

Done when: by voice, the owner can ask what needs attention in their mail, have Abogi draft a reply, and create, move or answer a meeting; every outward step is read back and runs only after "Abogi, approve" (ADR 0004).

### 1. Approval gate in the core — done (hosted agent v14)
Tools carry `outward` and a read-back sentence. The loop holds an outward call as the conversation's pending step; the hosted app runs it when the owner's next turn is an approval and drops it otherwise. Matching "Abogi, approve" must survive speech recognition ("A bogey, approve.").

### 2. Calendar actions — done, waiting on issue 4
Create an event (optionally with guests), move one, delete one, and accept, decline or tentatively accept an invitation. All outward. Needs the `calendar.events` scope instead of `calendar.events.readonly`.

### 3. Gmail: read, triage, draft, send — done, waiting on issue 4
Search mail with Gmail's query syntax (for example `is:unread newer_than:2d`), read one message, create a draft reply in its thread (inside the sandbox), and send a draft (outward). Needs the Gmail API enabled in the owner's Google Cloud project and the `gmail.readonly` and `gmail.compose` scopes.

### 4. Owner sign-in with the new scopes — ready-for-human
Enable the Gmail API in the Google Cloud project, then run the Google sign-in again (it updates Key Vault). Until then the new tools report a missing-permission error and Abogi says so.

### 5. Mail content is data, not instructions — done
Text inside an email must never be followed as an instruction. The gate already stops it causing an outward step; the instructions also say so.

### 6. Profile and history — needs-triage
What Abogi keeps about the owner between conversations (glossary: profile, history). Not needed for the done-when above; next after this.

## Findings (21 Nov 2026)
- `conversation_chain_id` is the turn's own response id unless steerable conversations are on, so the gate keys a pending step by conversation id (Voice Live sends one) or else by response id, looked up through `previous_response_id`.
- Foundry's Responses endpoint rejects system and developer messages in `input` ("Invalid value: ''"), so per-turn notes go into the instructions. A synthetic `function_call` plus `function_call_output` pair is accepted, which is how an approved step's result reaches the model.
- gpt-5-mini first asked "shall I?" before calling the tool, so nothing was held. The instructions now say to call an outward tool at once: the read-back is the question.
- Checked through Voice Live on v14: request held and read back by title and time; "yes please" does not run it (the model holds it again); "Abogi, approve" runs it (403 until the new scopes are granted).

## Steps 4 and 6, done on this branch (21 Nov 2026)
- Step 4: briefings at `ABOGI_BRIEFING_TIMES`; alerts from a code-only check every 5 minutes (meetings within 10 minutes, mail from `ABOGI_ALERT_FROM`). Hosted agent v15. Discord is still to do.
- Step 6: wake word "Hey Jarvis" with openWakeWord on the tray (Python 3.14; onnxruntime has no 3.15 wheels). Audio stays local until the wake word. Model loads and ignores silence; not yet tried by voice.
- The old tray client got "Conversation service API error 403" on every re-warm after the agent was redeployed. The client now starts a fresh conversation when that happens.

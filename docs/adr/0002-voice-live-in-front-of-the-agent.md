# Voice Live in front of the agent, not a realtime model as the brain

Foundry offers two routes to an interruptible voice conversation. The Azure OpenAI Realtime API makes a speech model run the conversation, with Abogi's loop reduced to a tool it calls. The Voice Live API handles speech-to-text, text-to-speech, turn detection and interruption, and passes text to a hosted agent. Abogi uses Voice Live so that the core stays text-only and stays in charge: the same loop serves voice, Discord and tests, and approvals are decided in one place.

## Consequences

- Voice is cloud-only. An offline mode would need a separate turn-based path.
- The spoken persona cannot be set through Voice Live's `instructions` when it fronts a custom agent; it has to come from the core.

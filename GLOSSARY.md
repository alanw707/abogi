# Abogi

Abogi is a voice-first personal chief of staff: an agent that acts on its owner's behalf across mail, calendar, research and code, and speaks up when something needs attention. Built on Microsoft Foundry; open source, with nothing personal in the repository.

## Language

### People

**Owner**:
The one person a running instance serves and acts for.
_Avoid_: User, account, customer

**Profile**:
The owner-readable, owner-editable record of facts and preferences about the owner, kept outside the repository.
_Avoid_: Memory, settings, persona

**History**:
The searchable record of past conversations and actions taken for the owner.
_Avoid_: Memory, logs

### Structure

**Core**:
The part every instance shares: the agent loop, voice, approvals, profile and history.
_Avoid_: Harness, engine, framework

**Skill**:
One job the chief of staff can do for the owner, built on one or more connectors.
_Avoid_: Plugin, feature, agent, tool

**Connector**:
Access to one external system using the owner's own credentials; it knows nothing about jobs.
_Avoid_: Plugin, integration, tool

**Channel**:
A way the chief of staff and the owner reach each other: the desktop voice client, or a remote messaging connector.
_Avoid_: Client, front end, interface

### Behaviour

**Outward step**:
Any action whose effect leaves the sandbox and is visible to someone else, such as sending a message or opening a pull request.
_Avoid_: Side effect, write, external action

**Approval**:
The owner's explicit go-ahead for one outward step; a spoken go-ahead is sufficient for any step.
_Avoid_: Confirmation, permission, consent

**Briefing**:
A scheduled spoken summary of what matters to the owner.
_Avoid_: Digest, report, summary

**Alert**:
An unscheduled interruption raised because an event crossed the owner's urgency setting.
_Avoid_: Notification, ping

**Preparation**:
Work the chief of staff starts unasked that stays inside the sandbox until the owner approves an outward step.
_Avoid_: Background task, autonomous work

**Quiet hours**:
The period in which alerts are held and delivered at the next briefing.
_Avoid_: Do not disturb, mute

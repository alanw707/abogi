# Approvals are enforced in code, not by the model

Until step 3 the approval rule lived only in the instructions: the model was told to read back an outward step and wait for "Abogi, approve". A model can misread a turn, follow text inside an email it is reading, or simply skip the rule, so for steps that reach other people (sending mail, creating or changing meetings) Abogi enforces it in the core instead.

Each tool says whether it is outward. When the model calls an outward tool, the loop does not run it: it holds the call as the conversation's one pending step and tells the model to read it back. The step runs only when the owner's next turn is an approval, and the core, not the model, runs it and then hands the model the result to report. Any other turn drops the pending step, so one approval covers exactly the step just read back.

Approval is the phrase "Abogi, approve", matched leniently because it arrives through speech recognition: the turn must be short, end in "approve" or "approved", and carry no negation. A plain "yes" does not count, which keeps a stray "yes" in hands-free mode from sending anything.

Pending steps are kept in memory, keyed by the conversation's chain id, for ten minutes. Losing one (a restart, a new hosted session) is safe: Abogi says nothing is waiting and the owner asks again. It can never cause a step to run without approval.

Drafting stays inside the sandbox and needs no approval: a Gmail draft is visible only to the owner until it is sent.

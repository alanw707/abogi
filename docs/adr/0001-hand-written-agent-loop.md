# Hand-written agent loop instead of Microsoft Agent Framework

Microsoft Agent Framework is the supported way to build agents on Foundry and ships its own harness. Abogi writes its own loop (model call, tool dispatch, context management, stop conditions) and uses Foundry only for models, hosting, tracing and evaluations. The project exists partly to learn how an agent loop works, and owning the loop keeps approvals, preparation and the profile as first-class concepts instead of framework extensions.

## Consequences

- Framework features such as orchestrations and durable workflows are not available for free; anything Abogi needs from them has to be built.
- The loop must stay thin. If it grows to re-implement the framework, revisit this decision.

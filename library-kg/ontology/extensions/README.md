# MAS-Lab ontology extensions

These TTL files declare native-path KG classes that **SCOPE (`ioc-scope-ontology`) does not yet ship**. They are not a second ontology.

| Source | Classes |
| --- | --- |
| `ioc-scope-ontology` (SCOPE) | Session, MASCall, AgentCall, LLMCall, ToolCall, ProcessingCall, State, Transition, MAS, Agent, LLM, Tool, Processing, Capability, CapabilityCall |
| This directory (pending upstream) | RAGQuery, MemoryCall, SkillCall, Skill, CallAnnotation, ContextContribution, ParallelGroup, Branch, GovernanceEvent, Worker, Run, Application, ThinkingCall |

Remove a class from `mas-kg-native-extensions.ttl` once it lands in SCOPE.

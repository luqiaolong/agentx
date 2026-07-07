# Spec: expert-delegation

## ADDED Requirements

### Requirement: Supervisor→Expert delegation mechanism uses LangGraph ToolNode
The system SHALL implement expert and subagent delegation as LangGraph tools within the Supervisor's ReAct graph, using the standard ToolNode execution flow. Delegation tools (`delegate_to_expert` and `delegate_to_subagent`) are ONLY available in the Supervisor (work mode), NOT in Experts or scenario teams.

#### Scenario: delegate_to_expert tool definition
- **WHEN** the Supervisor is built
- **THEN** it SHALL include a `delegate_to_expert` tool with parameters: `expert_name` (str), `task` (str), `context` (str, optional)
- **AND** the tool description SHALL clearly define available experts (currently `coding`) and their domains
- **AND** the tool description SHALL indicate when to use it (domain-specialized tasks better handled by experts)
- **AND** the expert list SHALL be dynamically derived from configuration to support future scenario extension

#### Scenario: delegate_to_subagent tool definition
- **WHEN** the Supervisor is built
- **THEN** it SHALL include a `delegate_to_subagent` tool with parameters: `agent_name` (str), `task` (str)
- **AND** the tool description SHALL clearly define available subagents (`rag`, `web`) and their capabilities

#### Scenario: invoke_agent_team tool does NOT exist
- **WHEN** the Supervisor is built
- **THEN** it SHALL NOT include any `invoke_agent_team` tool
- **AND** team collaboration SHALL only be accessible via mode switching to `<scenario>_team` modes

#### Scenario: Delegation produces standard tool_call events
- **WHEN** the Supervisor invokes any delegation tool
- **THEN** the system SHALL yield a `tool_call` SSE event with `name="delegate_to_expert"` or `name="delegate_to_subagent"`
- **AND** after the delegated agent completes, it SHALL yield a `tool_result` SSE event with the result
- **AND** the `source` field SHALL identify the delegated agent (e.g., "coding", "rag", "web")

#### Scenario: Nested delegation from Expert
- **WHEN** an Expert internally delegates to a subagent (e.g., coding Expert calls rag)
- **THEN** the subagent's events SHALL be yielded with `source` set to the subagent's own identifier ("rag" or "web")
- **AND** the frontend SHALL render them as nested within the Expert's execution trace

## ADDED Requirements

### Requirement: Expert has its own delegate_to_subagent tool (not delegate_to_expert)
The system SHALL provide a `delegate_to_subagent` tool within Experts (not `delegate_to_expert`), allowing Experts to invoke rag/web subagents for auxiliary tasks. Expert-to-Expert delegation is NOT supported (only Supervisor can delegate to Experts).

#### Scenario: Expert delegates to rag subagent
- **WHEN** the coding Expert needs knowledge base retrieval
- **THEN** it SHALL invoke `delegate_to_subagent` with `agent_name="rag"`
- **AND** the rag subagent's result SHALL be incorporated into the Expert's reasoning

#### Scenario: Expert-to-Expert delegation is not supported
- **WHEN** the coding Expert receives a task that might benefit from another Expert
- **THEN** the Expert SHALL NOT have a `delegate_to_expert` tool available
- **AND** it SHALL handle the task within its own domain or return a limitation notice
- **AND** only the Supervisor can delegate to Experts (Supervisor + Expert architecture)

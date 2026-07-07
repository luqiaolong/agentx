# Spec: primary-agent

## ADDED Requirements

### Requirement: Supervisor is the work scenario全能 agent with full toolset
The system SHALL provide a Supervisor bound to the `work` scenario that acts as an全能 agent with a complete toolset (filesystem read/write, CLI, Git, RAG retrieve, web search) plus delegation tools. The Supervisor SHALL autonomously decide whether to execute tasks itself or delegate to Experts (scenario-bound experts) / Subagents based on task nature. The Supervisor is the unified dispatch entry point for all Experts.

#### Scenario: Supervisor handles simple chat directly
- **WHEN** a user sends a simple greeting like "你好"
- **THEN** the Supervisor SHALL respond directly using the LLM without invoking any tools
- **AND** no delegation SHALL occur

#### Scenario: Supervisor executes file write itself
- **WHEN** a user asks "帮我创建一个 notes.txt 文件" in work mode
- **THEN** the Supervisor SHALL use the `write_file` tool directly (not delegate to coding Expert)
- **AND** the system SHALL yield an `approval_request` event before executing the write operation
- **AND** it SHALL only proceed after user approval

#### Scenario: Supervisor delegates to coding Expert for complex code tasks
- **WHEN** a user asks "帮我重构这个项目的认证模块"
- **THEN** the Supervisor SHALL invoke the `delegate_to_expert` tool with `expert_name="coding"`
- **AND** it SHALL pass the task description and context
- **AND** after receiving the Expert's result, it SHALL synthesize and present the final answer to the user

#### Scenario: Supervisor delegates to rag subagent
- **WHEN** a user asks "查一下知识库中关于API认证的文档"
- **THEN** the Supervisor SHALL invoke the `delegate_to_subagent` tool with `agent_name="rag"`
- **AND** it SHALL incorporate the retrieval result into its final response

#### Scenario: Supervisor delegates to web subagent
- **WHEN** a user asks "搜索一下最新的React版本"
- **THEN** the Supervisor SHALL invoke the `delegate_to_subagent` tool with `agent_name="web"`
- **AND** it SHALL incorporate the search result into its final response

#### Scenario: Supervisor does NOT trigger AgentTeam
- **WHEN** the Supervisor receives a complex multi-domain task
- **THEN** the Supervisor SHALL NOT invoke any `invoke_agent_team` tool (such tool SHALL NOT exist in work mode)
- **AND** the user SHALL switch to `coding_team` mode manually for team collaboration

## ADDED Requirements

### Requirement: Supervisor uses create_react_agent with interrupt_before approval
The system SHALL build the Supervisor using LangGraph's `create_react_agent`, with `interrupt_before` configured for dangerous tools (write_file, edit_file, cli_execute, git operations) to enforce the approval flow.

#### Scenario: Dangerous tools trigger approval
- **WHEN** the Supervisor invokes a dangerous tool (write_file, edit_file, cli_execute, git_write)
- **THEN** the system SHALL yield an `approval_request` event before execution
- **AND** the tool SHALL only execute after user approval
- **AND** rejection SHALL abort the tool call and inform the Supervisor

#### Scenario: Read-only tools do not trigger approval
- **WHEN** the Supervisor invokes a read-only tool (read_file, list_dir, glob, grep, rag_retrieve, web_search)
- **THEN** the system SHALL execute the tool immediately without approval
- **AND** the result SHALL be returned to the Supervisor

## ADDED Requirements

### Requirement: Supervisor supports @mention syntax for forced delegation
The system SHALL support `@<agent-name>` syntax in user messages within work mode to force delegation to a specific Expert or Subagent, overriding the Supervisor's autonomous decision.

#### Scenario: @coding forces Expert delegation
- **WHEN** a user sends a message starting with "@coding 帮我review这段代码" in work mode
- **THEN** the system SHALL parse the @mention and route directly to the coding Expert
- **AND** the message content after "@coding" SHALL be passed as the task
- **AND** the Supervisor SHALL NOT make its own routing decision

#### Scenario: @rag forces subagent delegation
- **WHEN** a user sends a message starting with "@rag 查询用户认证流程" in work mode
- **THEN** the system SHALL parse the @mention and route directly to the rag subagent
- **AND** the result SHALL be returned to the Supervisor for synthesis

#### Scenario: Unknown @mention falls back to Supervisor
- **WHEN** a user sends a message with an unknown @mention like "@unknown 任务" in work mode
- **THEN** the system SHALL strip the @mention and process the message normally through the Supervisor
- **AND** it SHALL log a warning about the unknown agent name

#### Scenario: @mention in non-work mode is stripped
- **WHEN** a user sends a message with @mention in `coding` or `coding_team` mode
- **THEN** the system SHALL strip the @mention before processing
- **AND** it SHALL process the message normally in the current mode

## ADDED Requirements

### Requirement: Supervisor is the unified dispatch entry for all Experts
The system SHALL treat the Supervisor as the unified dispatch entry point for all Experts. When a new scenario (e.g., research) is added in the future, the Supervisor SHALL automatically gain the ability to delegate to the new Expert via the `delegate_to_expert` tool, without modifying the Supervisor's core logic.

#### Scenario: Supervisor delegates to future research Expert
- **WHEN** a future `research` Expert is added to the system
- **THEN** the Supervisor SHALL be able to delegate to it via `delegate_to_expert` with `expert_name="research"`
- **AND** no modification to the Supervisor's dispatch logic SHALL be required
- **AND** the Expert list in `delegate_to_expert` tool description SHALL be dynamically derived from configuration

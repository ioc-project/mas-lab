//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/**
 * Events Plugin
 * 
 * Handles core MAS events (audit, llm_call, llm_response, tool_call, tool_result, etc.)
 * and manages node/edge states accordingly.
 */

class EventsPlugin extends Plugin {
    constructor(config = {}) {
        super('events', config);
    }

    /**
     * Play a one-shot CSS animation on a node's circle element.
     * Removes the class and calls onEnd() after durationMs, regardless of animationend timing.
     */
    _playNodeAnim(nodeId, animClass, durationMs, onEnd) {
        const ONE_SHOT = ['node-active-collapse', 'node-active-expand'];
        const svg = typeof document !== 'undefined' && document.getElementById('topology');
        if (!svg) { onEnd?.(); return; }
        const circle = svg.querySelector(`g[data-node-id="${nodeId}"] circle`);
        if (!circle) { onEnd?.(); return; }
        ONE_SHOT.forEach(c => circle.classList.remove(c));
        circle.classList.add(animClass);
        setTimeout(() => { circle.classList.remove(animClass); onEnd?.(); }, durationMs);
    }

    async handleEvent(event, context) {
        if (!this.shouldHandle(event)) return true;

        const { kind } = event;
        const agentId = context.resolveAgentId(event);
        const target = context.resolveTarget(event);
        const { visualNow, isInitialLoad, pulseDuration, triggerFlowPulse } = context;

        // Handle flow visualization and activation
        await this.handleFlow(event, context, agentId, target);

        // State transitions based on event kind
        switch (kind) {
            case 'audit':
                await this.handleAudit(event, context, agentId);
                break;

            case 'llm_call_start':
            case 'llm_call':
                await this.handleLLMCall(event, context, agentId);
                break;

            case 'llm_call_end':
            case 'llm_response':
                await this.handleLLMResponse(event, context, agentId);
                break;

            case 'user_input': {
                // Receiving agent starts waiting as soon as it gets the user message
                const { setNodePersistentClass } = context;
                const to = agentId || (() => {
                    const topo = context.topology();
                    const entryEdge = topo?.edges.find(e => e.from === 'user');
                    return entryEdge?.to || topo?.nodes.find(n => n.type === 'agent')?.id || null;
                })();
                if (to) setNodePersistentClass(to, 'node-waiting');
                break;
            }

            case 'user_response':
            case 'agent_to_user':
                await this.handleUserResponse(event, context, agentId);
                break;

            case 'tool_call_start':
            case 'tool_call':
                await this.handleToolCall(event, context, agentId, target);
                break;

            case 'tool_call_end':
            case 'tool_result':
                await this.handleToolResult(event, context, agentId, target);
                break;

            case 'memory_read':
            case 'memory_write':
                await this.handleMemoryOp(event, context, agentId, target);
                break;

            case 'memory_result':
                await this.handleMemoryResult(event, context, agentId, target);
                break;

            case 'routing':
                // Delegating agent waits for sub-agent result
                await this.handleDelegation(event, context, agentId);
                break;

            case 'agent_to_agent':
            case 'agent_remote_call':
            case 'agent_remote_call_start':
                await this.handleAgentCall(event, context, agentId, target);
                break;

            case 'agent_to_agent_result':
            case 'agent_remote_result':
            case 'agent_remote_call_end':
                await this.handleAgentResult(event, context, agentId, target);
                break;

            case 'tool_unavailable':
            case 'memory_unavailable':
            case 'skills_unavailable':
            case 'prompt_unavailable':
                await this.handleUnavailable(event, context, agentId, target);
                break;
        }

        // Add to activity log and timeline
        context.addToLog(event, !isInitialLoad);
        context.addToTimeline(event);

        return true; // Continue processing
    }

    async handleFlow(event, context, agentId, target) {
        const { kind } = event;
        const {
            isInitialLoad, pulseDuration, triggerFlowPulse, enqueueFlowPulse,
            setNodeActive, setEdgeActive,
            setNodePersistentClass, clearNodePersistentClass,
        } = context;

        // Resolve per-agent LLM node from topology; skip flow if no real LLM node exists
        const getLlmNode = () => {
            if (!agentId) return null;
            const agentLlmId = `${agentId}__llm`;
            const topo = context.topology();
            if (topo?.nodes.find(n => n.id === agentLlmId)) return agentLlmId;
            // Only fall back to an explicit named target — never to 'llm:default'
            if (target && target !== 'llm:default') return target;
            return null;
        };

        // Resolve flow direction — server emits *_start / *_end variants
        let flow = null;

        if (kind === 'user_input') {
            // agentId may be null if the server event lacks agent_id — fall back to topology entry agent
            const to = agentId || (() => {
                const topo = context.topology();
                const entryEdge = topo?.edges.find(e => e.from === 'user');
                return entryEdge?.to || topo?.nodes.find(n => n.type === 'agent')?.id || null;
            })();
            if (to) flow = { from: 'user', to };
        } else if (kind.includes('llm') && !kind.includes('end') && !kind.includes('response') && agentId) {
            // llm_call_start / llm_call → agent → LLM
            flow = { from: agentId, to: getLlmNode() };
        } else if ((kind.includes('llm') && (kind.includes('end') || kind === 'llm_response')) && agentId) {
            // llm_call_end / llm_response → LLM → agent
            flow = { from: getLlmNode(), to: agentId };
        } else if ((kind === 'user_response' || kind === 'human_interaction' || kind === 'agent_to_user') && agentId) {
            flow = { from: agentId, to: 'user' };
        } else if (kind.includes('tool') && !kind.includes('end') && !kind.includes('result') && agentId && target) {
            // tool_call_start / tool_call → agent → tool
            flow = { from: agentId, to: target };
        } else if (kind.includes('tool') && (kind.includes('end') || kind.includes('result')) && agentId && target) {
            // tool_call_end / tool_result → tool → agent
            flow = { from: target, to: agentId };
        } else if ((kind === 'memory_read' || kind === 'memory_write') && agentId && target) {
            flow = { from: agentId, to: target };
        } else if (kind === 'memory_result' && agentId && target) {
            flow = { from: target, to: agentId };
        } else if (kind === 'routing') {
            // Agent-to-agent delegation
            const from = event.source_agent_id || agentId;
            const to = event.target_agent_id || event.target;
            if (from && to) flow = { from, to };
        } else if ((kind === 'agent_to_agent' || (kind.includes('agent-remote') && !kind.includes('result'))) && agentId && target) {
            flow = { from: agentId, to: target };
        } else if ((kind === 'agent_to_agent_result' || (kind.includes('agent-remote') && kind.includes('result'))) && agentId && target) {
            flow = { from: agentId, to: target };
        }

        if (!flow || !flow.from || !flow.to) {
            // No directed flow: mark nodes active only in replay mode
            if (isInitialLoad) {
                if (agentId) setNodeActive(agentId, Date.now());
                if (target) setNodeActive(target, Date.now());
            }
            return;
        }

        const responseKinds = new Set(['llm_call_end', 'llm_response', 'tool_call_end', 'tool_result', 'memory_result', 'agent_to_agent_result', 'user_response', 'human_interaction', 'agent_to_user']);
        const flowType = responseKinds.has(kind) ? 'response' : 'request';

        if (isInitialLoad) {
            // Replay mode: do NOT call setNodeActive — all historical events would
            // enqueue simultaneous 700ms timers that expire together, making every
            // node flash red at once on page load. Persistent waiting states are
            // correctly rebuilt by handleLLMCall / handleToolCall etc. (those run
            // regardless of isInitialLoad). Nothing extra needed here.
            return;
        }

        // Live mode: FIFO sequential animation
        //   source collapses → packet travels → dest expands into pulse
        await enqueueFlowPulse(async () => {
            const now = Date.now();
            // 1. Source dispatches — clear persistent red and play collapse shrink
            clearNodePersistentClass(flow.from, 'node-active');
            this._playNodeAnim(flow.from, 'node-active-collapse', 220);
            // 2. Edge lit for packet travel duration
            setEdgeActive(flow.from, flow.to, now, flowType, 1, pulseDuration + 100);
            // 3. Packet travels — when it touches dest border (~72%), play expand
            const earlyMs = Math.round(pulseDuration * 0.72);
            let earlyFired = false;
            const earlyTimer = setTimeout(() => {
                earlyFired = true;
                clearNodePersistentClass(flow.to, 'node-waiting');
                this._playNodeAnim(flow.to, 'node-active-expand', 480, () => {
                    setNodePersistentClass(flow.to, 'node-active');
                });
            }, earlyMs);
            await triggerFlowPulse(flow.from, flow.to, pulseDuration);
            clearTimeout(earlyTimer);
            // 4. Fallback for very short pulses where early timer didn't fire in time
            if (!earlyFired) {
                clearNodePersistentClass(flow.to, 'node-waiting');
                this._playNodeAnim(flow.to, 'node-active-expand', 480, () => {
                    setNodePersistentClass(flow.to, 'node-active');
                });
            }
            // 5. Extinguish edge (packet arrived)
            if (context.clearEdgeActive) context.clearEdgeActive(flow.from, flow.to);
            // 6. Brief hold so expand animation has room to breathe
            await new Promise(r => setTimeout(r, 520));
        });
    }

    async handleAudit(event, context, agentId) {
        // Audit is a log event only — no visual flow animation
        this.emit('audit', { event, agentId });
    }

    async handleLLMCall(event, context, agentId) {
        const { setNodePersistentClass, clearNodePersistentClass } = context;
        // Agent waits for LLM reply — spinner on agent; LLM node gets active via handleFlow packet (no spinner on LLM)
        if (agentId) { setNodePersistentClass(agentId, 'node-waiting'); clearNodePersistentClass(agentId, 'node-active'); }
        this.emit('llm_call', { event, agentId });
    }

    async handleLLMResponse(event, context, agentId) {
        const { clearNodePersistentClass } = context;
        // Clear agent waiting — handleFlow llm_call_end will set node-active back on agent
        if (agentId) clearNodePersistentClass(agentId, 'node-waiting');
        this.emit('llm_response', { event, agentId });
    }

    async handleUserResponse(event, context, agentId) {
        const { clearNodePersistentClass } = context;
        // Agent finished — clear both waiting and active (it is now idle)
        if (agentId) clearNodePersistentClass(agentId, 'node-waiting');
        if (agentId) clearNodePersistentClass(agentId, 'node-active');
        this.emit('user_response', { event, agentId });
    }

    async handleDelegation(event, context, agentId) {
        const { setNodePersistentClass, clearNodePersistentClass } = context;
        // Delegating agent waits while sub-agent processes
        const from = event.source_agent_id || agentId;
        if (from) { setNodePersistentClass(from, 'node-waiting'); clearNodePersistentClass(from, 'node-active'); }
        this.emit('delegation', { event, agentId });
    }

    async handleAgentCall(event, context, agentId, target) {
        const { setNodePersistentClass, clearNodePersistentClass } = context;
        // Caller waits for the callee to finish
        if (agentId) { setNodePersistentClass(agentId, 'node-waiting'); clearNodePersistentClass(agentId, 'node-active'); }
        this.emit('agent_call', { event, agentId, target });
    }

    async handleAgentResult(event, context, agentId, target) {
        const { clearNodePersistentClass } = context;
        // Result arrived — clear spinner on the original caller (stored in target for result events)
        const caller = target || event.target_agent_id || event.to;
        if (caller) clearNodePersistentClass(caller, 'node-waiting');
        // Sub-agent dispatched its result — always release active token regardless of flow animation
        if (agentId) {
            clearNodePersistentClass(agentId, 'node-active');
            clearNodePersistentClass(agentId, 'node-waiting');
        }
        this.emit('agent_result', { event, agentId, target });
    }

    async handleToolCall(event, context, agentId, target) {
        const { setNodePersistentClass, clearNodePersistentClass } = context;
        // Agent waits (spinner) while tool runs
        if (agentId) { setNodePersistentClass(agentId, 'node-waiting'); clearNodePersistentClass(agentId, 'node-active'); }
        this.emit('tool_call', { event, agentId, target });
    }

    async handleToolResult(event, context, agentId, target) {
        const { clearNodePersistentClass } = context;
        // Clear spinner; handleFlow pulse marks agent as active
        if (agentId) clearNodePersistentClass(agentId, 'node-waiting');
        this.emit('tool_result', { event, agentId, target });
    }

    async handleMemoryOp(event, context, agentId, target) {
        const { setNodePersistentClass, clearNodePersistentClass } = context;
        if (agentId) { setNodePersistentClass(agentId, 'node-waiting'); clearNodePersistentClass(agentId, 'node-active'); }
        this.emit('memory_operation', { event, agentId, target });
    }

    async handleMemoryResult(event, context, agentId, target) {
        const { clearNodePersistentClass } = context;
        if (agentId) clearNodePersistentClass(agentId, 'node-waiting');
        this.emit('memory_result', { event, agentId, target });
    }

    async handleUnavailable(event, context, agentId, target) {
        const { visualNow, setNodeStatus } = context;
        
        if (target) {
            setNodeStatus(target, 'unavailable', visualNow);
        } else if (agentId) {
            setNodeStatus(agentId, 'unavailable', visualNow);
        }
        // No fallback to a hardcoded node — if we can't identify the node, skip.

        this.emit('unavailable', { event, agentId, target });
    }
}

if (typeof window !== 'undefined') {
    window.EventsPlugin = EventsPlugin;
}

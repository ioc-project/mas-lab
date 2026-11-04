//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/**
 * Observability Plugin
 * 
 * Handles OTEL (OpenTelemetry) visualization and traces
 */

class ObservabilityPlugin extends Plugin {
    constructor(config = {}) {
        super('observability', config);
        this.showOtel = false;
    }

    init(registry) {
        super.init(registry);
        
        // Register toggle control
        this.toggleOtel = document.getElementById('toggle-otel');
        if (this.toggleOtel) {
            this.showOtel = this.toggleOtel.checked;
            this.toggleOtel.addEventListener('change', () => {
                this.showOtel = this.toggleOtel.checked;
                this.emit('visibility_changed', { visible: this.showOtel });
                
                // Trigger topology redraw
                const topology = this.getSharedState('cachedTopology');
                if (topology) {
                    this.registry.executeHook('topology:redraw', topology);
                }
            });
        }
    }

    async handleEvent(event, context) {
        if (!this.shouldHandle(event) || !this.showOtel) return true;

        // Visualize OTEL flow
        if (!context.isInitialLoad) {
            await this.visualizeOtelFlow(event, context);
        }

        return true;
    }

    async visualizeOtelFlow(event, context) {
        const { 
            agentId,
            target,
            triggerFreePulse
        } = context;

        // Find active node (agent, tool, user associated with this event)
        const activeNodeId = agentId || target || (event.kind === 'audit' ? 'user' : null);

        if (!activeNodeId) return;

        // Find nearest infrastructure node
        const topology = context.topology();
        if (!topology) return;
        
        const infraNodes = topology.nodes.filter(n => n.type === 'infra');
        if (infraNodes.length === 0) return;

        // Pick first infra node (could be made smarter with nearest-node logic)
        const infraId = infraNodes[0].id;

        // Send OTEL bubble (slightly slower to distinguish from main flow)
        triggerFreePulse(activeNodeId, infraId, 400, 'otel-packet');

        this.emit('trace_sent', { from: activeNodeId, to: infraId, event });
    }

    shouldHandle(event) {
        return this.enabled && this.showOtel;
    }

    setVisibility(visible) {
        this.showOtel = visible;
        if (this.toggleOtel) {
            this.toggleOtel.checked = visible;
        }
        this.emit('visibility_changed', { visible });
    }

    isVisible() {
        return this.showOtel;
    }
}

if (typeof window !== 'undefined') {
    window.ObservabilityPlugin = ObservabilityPlugin;
}

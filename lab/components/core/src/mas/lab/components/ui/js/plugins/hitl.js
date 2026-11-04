//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/**
 * Human-In-The-Loop (HITL) Plugin
 * 
 * Handles human_interaction events and provides visual feedback
 * for agent-to-user communication.
 */

class HITLPlugin extends Plugin {
    constructor(config = {}) {
        super('hitl', config);
        this.interactions = [];
    }

    init(registry) {
        super.init(registry);
        
        // Register UI elements
        this.messageContainer = document.getElementById('hitl-messages');
        if (!this.messageContainer) {
            this.createMessageContainer();
        }
    }

    createMessageContainer() {
        // Create a floating message container if it doesn't exist
        const container = document.createElement('div');
        container.id = 'hitl-messages';
        container.className = 'hitl-messages';
        document.body.appendChild(container);
        this.messageContainer = container;
    }

    async handleEvent(event, context) {
        if (!this.shouldHandle(event)) return true;

        if (event.kind === 'human_interaction' || event.type === 'human_interaction' || event.kind === 'user_response') {
            await this.processHumanInteraction(event, context);
            return true; // Continue processing
        }

        return true;
    }

    shouldHandle(event) {
        return this.enabled && (
            event.kind === 'human_interaction' || 
            event.type === 'human_interaction' ||
            event.kind === 'user_response'
        );
    }

    async processHumanInteraction(event, context) {
        const agentId = event.agent_id || this.resolveAgentId(event);
        const content = event.payload?.content || event.content || '';

        // Store interaction
        this.interactions.push({
            timestamp: event.timestamp || Date.now() / 1000,
            agentId,
            content,
            event
        });

        // Visual pulse (agentId → user) is handled by EventsPlugin.handleFlow via the
        // FIFO enqueueFlowPulse queue. Firing another triggerFlowPulse here would produce
        // a second simultaneous bump on the same edge.

        // Show notification
        this.showNotification(agentId, content);

        // Emit custom event for other plugins
        this.emit('interaction', { agentId, content, event });
    }

    showNotification(agentId, content) {
        if (!this.messageContainer) return;

        const notification = document.createElement('div');
        notification.className = 'hitl-notification';
        notification.innerHTML = `
            <div class="hitl-notification-header">
                <span class="hitl-agent-badge">${agentId || 'Agent'}</span>
                <span class="hitl-timestamp">${new Date().toLocaleTimeString()}</span>
            </div>
            <div class="hitl-notification-content">${this.escapeHtml(content)}</div>
        `;

        this.messageContainer.appendChild(notification);

        // Auto-dismiss after 10 seconds
        setTimeout(() => {
            notification.classList.add('hitl-notification-fade');
            setTimeout(() => notification.remove(), 300);
        }, 10000);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    resolveAgentId(event) {
        return event.agent_id || 
               event.payload?.agent_id || 
               event.source?.agent_id ||
               null;
    }

    getInteractions() {
        return this.interactions;
    }

    clearInteractions() {
        this.interactions = [];
    }
}

if (typeof window !== 'undefined') {
    window.HITLPlugin = HITLPlugin;
}

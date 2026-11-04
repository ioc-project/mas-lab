//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/**
 * Metrics Plugin
 * 
 * Tracks and displays metrics (cost, tokens, latency, quality, etc.)
 */

class MetricsPlugin extends Plugin {
    constructor(config = {}) {
        super('metrics', config);
        this.metricsState = {
            cost: 0,
            tokens: 0,
            promptTokens: 0,
            completionTokens: 0,
            events: 0,
            duration: 0,
            toolCalls: 0,
            reliability: 100,
            quality: 0,
            performance: 0,
            lastValues: new Map()
        };
        this.sessionStartTime = null;
    }

    init(registry) {
        super.init(registry);
        this.sessionStartTime = Date.now();
        
        // Register metric UI elements
        this.elements = {
            cost: document.getElementById('metric-cost'),
            tokens: document.getElementById('metric-tokens'),
            events: document.getElementById('metric-events'),
            duration: document.getElementById('metric-duration'),
            tools: document.getElementById('metric-tools'),
            reliability: document.getElementById('metric-reliability'),
            quality: document.getElementById('metric-quality'),
            perf: document.getElementById('metric-perf')
        };
    }

    async handleEvent(event, context) {
        if (!this.shouldHandle(event)) return true;

        this.updateFromEvent(event);
        this.refreshDisplay();

        return true;
    }

    updateFromEvent(event) {
        // Increment event counter
        this.metricsState.events++;

        // Update token counts from LLM events
        if (event.kind === 'llm_response') {
            const usage = event.payload?.usage || event.usage || {};
            this.metricsState.promptTokens += usage.prompt_tokens || 0;
            this.metricsState.completionTokens += usage.completion_tokens || 0;
            this.metricsState.tokens = this.metricsState.promptTokens + this.metricsState.completionTokens;
            
            // Estimate cost (rough estimate: $0.01 per 1000 tokens)
            this.metricsState.cost = (this.metricsState.tokens / 1000) * 0.01;
        }

        // Track tool/memory calls
        if (event.kind === 'tool_call' || event.kind === 'memory_read' || event.kind === 'memory_write') {
            this.metricsState.toolCalls++;
        }

        // Track reliability (failures)
        if (event.kind === 'tool_unavailable' || event.kind === 'memory_unavailable' || event.kind === 'skills_unavailable') {
            this.metricsState.reliability = Math.max(0, this.metricsState.reliability - 5);
        }

        // Update duration
        if (this.sessionStartTime) {
            this.metricsState.duration = (Date.now() - this.sessionStartTime) / 1000;
        }

        // Emit metric update event
        this.emit('updated', this.metricsState);
    }

    refreshDisplay() {
        this.updateMetricUI('cost', this.metricsState.cost, (v) => `$${v.toFixed(4)}`);
        this.updateMetricUI('tokens', this.metricsState.tokens, (v) => v.toString());
        this.updateMetricUI('events', this.metricsState.events, (v) => v.toString());
        this.updateMetricUI('duration', this.metricsState.duration, (v) => `${Math.floor(v)}s`);
        this.updateMetricUI('tools', this.metricsState.toolCalls, (v) => v.toString());
        this.updateMetricUI('reliability', this.metricsState.reliability, (v) => `${v}%`);
        
        // Quality and performance are placeholders for now
        if (this.elements.quality) {
            this.elements.quality.textContent = '--';
        }
        if (this.elements.perf) {
            const perf = 9400 + Math.floor((this.metricsState.duration % 10) * 15);
            this.elements.perf.textContent = `${perf} ms`;
        }
    }

    updateMetricUI(metricId, value, formatter, effect = 'faint') {
        const element = this.elements[metricId];
        if (!element) return;

        const formattedValue = formatter ? formatter(value) : value.toString();
        const lastValue = this.metricsState.lastValues.get(metricId);

        if (lastValue !== value) {
            element.textContent = formattedValue;
            this.metricsState.lastValues.set(metricId, value);

            // Visual effect
            if (effect === 'faint') {
                element.classList.add('highlight-faint');
                setTimeout(() => element.classList.remove('highlight-faint'), 600);
            } else if (effect === 'bright') {
                element.classList.add('highlight-bright');
                setTimeout(() => element.classList.remove('highlight-bright'), 1200);
            }
        }
    }

    reset() {
        this.metricsState = {
            cost: 0,
            tokens: 0,
            promptTokens: 0,
            completionTokens: 0,
            events: 0,
            duration: 0,
            toolCalls: 0,
            reliability: 100,
            quality: 0,
            performance: 0,
            lastValues: new Map()
        };
        this.sessionStartTime = Date.now();
        this.refreshDisplay();
    }

    getMetrics() {
        return { ...this.metricsState };
    }
}

if (typeof window !== 'undefined') {
    window.MetricsPlugin = MetricsPlugin;
}

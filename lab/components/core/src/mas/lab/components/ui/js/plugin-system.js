//  Copyright (c) 2026 Cisco Systems, Inc. and its affiliates
//  SPDX-License-Identifier: Apache-2.0
/**
 * Plugin System for MAS Skeleton UI
 * 
 * Each plugin handles a specific concern (HITL, metrics, events, topology, etc.)
 * and implements a standard interface for event processing, rendering, and state management.
 */

class PluginRegistry {
    constructor() {
        this.plugins = new Map();
        this.hooks = new Map();
        this.state = new Map();
    }

    /**
     * Register a plugin
     * @param {Plugin} plugin - Plugin instance
     */
    register(plugin) {
        if (!plugin || !plugin.name) {
            console.error('Invalid plugin: must have a name property');
            return;
        }
        
        const name = plugin.name;
        if (this.plugins.has(name)) {
            console.warn(`Plugin ${name} already registered, replacing`);
        }
        this.plugins.set(name, plugin);
        
        // Initialize plugin
        if (typeof plugin.init === 'function') {
            plugin.init(this);
        }
        
        console.log(`Plugin registered: ${name}`);
    }

    /**
     * Get a plugin by name
     * @param {string} name - Plugin name
     * @returns {Plugin|undefined}
     */
    get(name) {
        return this.plugins.get(name);
    }

    /**
     * Process an event through all plugins
     * @param {Object} event - Event object
     * @param {Object} context - Event context (topology, visualNow, etc.)
     */
    async processEvent(event, context) {
        for (const [name, plugin] of this.plugins) {
            if (typeof plugin.handleEvent === 'function') {
                try {
                    const shouldContinue = await plugin.handleEvent(event, context);
                    if (shouldContinue === false) {
                        break; // Plugin consumed the event
                    }
                } catch (err) {
                    console.error(`Plugin ${name} failed to process event:`, err);
                }
            }
        }
    }

    /**
     * Register a hook
     * @param {string} hookName - Hook name (e.g., "beforeRender", "afterEvent")
     * @param {Function} callback - Hook callback
     */
    registerHook(hookName, callback) {
        if (!this.hooks.has(hookName)) {
            this.hooks.set(hookName, []);
        }
        this.hooks.get(hookName).push(callback);
    }

    /**
     * Execute a hook
     * @param {string} hookName - Hook name
     * @param {...any} args - Hook arguments
     */
    async executeHook(hookName, ...args) {
        const callbacks = this.hooks.get(hookName) || [];
        for (const callback of callbacks) {
            try {
                await callback(...args);
            } catch (err) {
                console.error(`Hook ${hookName} failed:`, err);
            }
        }
    }

    /**
     * Get/set shared state
     */
    setState(key, value) {
        this.state.set(key, value);
    }

    getState(key, defaultValue = null) {
        return this.state.has(key) ? this.state.get(key) : defaultValue;
    }
}

/**
 * Base Plugin class
 */
class Plugin {
    constructor(name, config = {}) {
        this.name = name;
        this.config = config;
        this.enabled = config.enabled !== false;
        this.registry = null;
    }

    /**
     * Initialize plugin (called when registered)
     * @param {PluginRegistry} registry
     */
    init(registry) {
        this.registry = registry;
    }

    /**
     * Handle an event
     * @param {Object} event - Event object
     * @param {Object} context - Event context
     * @returns {boolean} - Return false to stop event propagation
     */
    async handleEvent(event, context) {
        // Override in subclass
        return true;
    }

    /**
     * Check if plugin should process this event
     * @param {Object} event
     * @returns {boolean}
     */
    shouldHandle(event) {
        return this.enabled;
    }

    /**
     * Emit a custom event
     */
    emit(eventName, data) {
        if (this.registry) {
            this.registry.executeHook(`plugin:${this.name}:${eventName}`, data);
        }
    }

    /**
     * Get shared state
     */
    getSharedState(key, defaultValue) {
        return this.registry ? this.registry.getState(key, defaultValue) : defaultValue;
    }

    /**
     * Set shared state
     */
    setSharedState(key, value) {
        if (this.registry) {
            this.registry.setState(key, value);
        }
    }
}

// Export for use in other modules
if (typeof window !== 'undefined') {
    window.PluginRegistry = PluginRegistry;
    window.Plugin = Plugin;
}

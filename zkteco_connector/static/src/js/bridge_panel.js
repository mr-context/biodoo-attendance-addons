/** @odoo-module **/

// Augmente le dashboard NATS (core_nats) d'un panneau « Statut du bridge »,
// alimenté par NATS request/reply côté serveur (/zkteco/bridge/status).
// core_nats reste générique : tout le code bridge vit ici.

import { onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { patch } from "@web/core/utils/patch";

const BRIDGE_POLL_MS = 30000;
const NatsDashboard = registry.category("actions").get("nats_dashboard");

patch(NatsDashboard.prototype, {
    setup() {
        super.setup();
        Object.assign(this.state, {
            bridge: null,           // dict PanelStatus, ou null
            bridgeDevices: [],
            bridgeReachable: null,  // null = en cours, true/false ensuite
            bridgeError: null,
            bridgeOpen: false,      // panneau enroulé par défaut
        });

        onMounted(() => {
            this._refreshBridge();
            this._bridgeTimer = setInterval(() => this._refreshBridge(), BRIDGE_POLL_MS);
        });
        onWillUnmount(() => {
            if (this._bridgeTimer) {
                clearInterval(this._bridgeTimer);
            }
        });
    },

    async _refreshBridge() {
        try {
            const resp = await fetch("/zkteco/bridge/status", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ jsonrpc: "2.0", method: "call", id: 1, params: {} }),
            });
            const json = await resp.json();
            const d = json.result || {};
            if (!d.reachable) {
                Object.assign(this.state, {
                    bridgeReachable: false,
                    bridgeError: d.error || "Bridge hors ligne.",
                    bridge: null,
                    bridgeDevices: [],
                });
                return;
            }
            Object.assign(this.state, {
                bridgeReachable: true,
                bridgeError: null,
                bridge: d.status,
                bridgeDevices: d.devices || [],
            });
        } catch (e) {
            Object.assign(this.state, {
                bridgeReachable: false,
                bridgeError: String(e),
                bridge: null,
                bridgeDevices: [],
            });
        }
    },

    // « il y a 39 s » calculé côté client à partir du RFC3339 du bridge.
    bridgeLastContact() {
        const s = this.state.bridge && this.state.bridge.server_last_contact;
        if (!s) {
            return "jamais contacté";
        }
        const delta = Math.max(0, Math.floor((Date.now() - new Date(s).getTime()) / 1000));
        if (delta < 60) {
            return `il y a ${delta} s`;
        }
        if (delta < 3600) {
            return `il y a ${Math.floor(delta / 60)} min`;
        }
        return `il y a ${Math.floor(delta / 3600)} h`;
    },

    bridgeExpiry() {
        const s = this.state.bridge && this.state.bridge.expires_at;
        return s ? new Date(s).toLocaleDateString("fr-FR") : "–";
    },

    bridgeDevicePct() {
        const b = this.state.bridge;
        if (!b || !b.max_devices) {
            return 0;
        }
        return Math.min(100, Math.round(((b.device_count || 0) * 100) / b.max_devices));
    },

    toggleBridge() {
        this.state.bridgeOpen = !this.state.bridgeOpen;
    },

    bridgeStateBadge(state) {
        return {
            approved: "text-bg-success",
            held: "text-bg-warning",
            pending: "text-bg-secondary",
        }[state] || "text-bg-light";
    },
});
/** @odoo-module **/

import { Component, useState, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";

const POLL_MS = 2000;

class NatsDashboard extends Component {
    static template = "core_nats.NatsDashboard";
    static props = ["*"];

    setup() {
        this.state = useState({
            loading:    true,
            connected:  false,
            url:        "",
            total:      0,
            rate:       0,
            subs:       [],
            feed:       [],
            lastUpdate: null,
            lastFeedTs: null,
            hasNewMsg:  false,
            error:      null,
        });

        this._timer = null;

        onMounted(() => {
            this._refresh();
            this._timer = setInterval(() => this._refresh(), POLL_MS);
        });
        onWillUnmount(() => {
            if (this._timer) clearInterval(this._timer);
        });
    }

    async _refresh() {
        try {
            const resp = await fetch("/nats/dashboard/data", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ jsonrpc: "2.0", method: "call", id: 1, params: {} }),
            });
            const json = await resp.json();
            if (json.error) {
                this.state.error   = json.error.data?.message || json.error.message;
                this.state.loading = false;
                return;
            }
            const d    = json.result || {};
            const feed = d.recent_messages || [];
            const newestTs = feed.length > 0 ? feed[0].ts : null;

            Object.assign(this.state, {
                loading:    false,
                connected:  !!d.connected,
                url:        d.url || "",
                total:      d.total_messages || 0,
                rate:       d.rate_per_min   || 0,
                subs:       d.subscriptions  || [],
                feed,
                lastUpdate: new Date().toLocaleTimeString(),
                hasNewMsg:  newestTs !== null && newestTs !== this.state.lastFeedTs,
                lastFeedTs: newestTs,
                error:      d.error || null,
            });
        } catch (e) {
            this.state.error   = String(e);
            this.state.loading = false;
        }
    }

    // Returns CSS classes for a feed row.
    feedRowClass(index) {
        return index === 0 && this.state.hasNewMsg
            ? "o_nats_feed_row o_nats_feed_new"
            : "o_nats_feed_row";
    }

    // Returns a CSS class based on the NATS subject prefix for color-coding.
    subjectClass(subject) {
        if (subject.startsWith("zkteco.ta.")) return "subject-ta";
        if (subject.startsWith("zkteco.ac.")) return "subject-ac";
        return "subject-other";
    }
}

registry.category("actions").add("nats_dashboard", NatsDashboard);

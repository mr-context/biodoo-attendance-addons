/** @odoo-module **/
/**
 * Client Action — Carte itinéraire d'un employé pour une période.
 * Vert = objectif atteint, Orange = partiel, Rouge = pas visité.
 */
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const OSM_TILES   = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const DEFAULT_LAT = 36.7372;
const DEFAULT_LNG = 3.0869;

const STATUS_COLOR = {
    full:    '#198754',   // vert
    partial: '#fd7e14',   // orange
    none:    '#dc3545',   // rouge
};
const STATUS_ICON = {
    full: '✓', partial: '~', none: '✗',
};

class CheckpointDayMap extends Component {
    static template = "hr_face_attendance.CheckpointDayMap";
    static props = ["action", "actionStack?"];

    setup() {
        this.actionService = useService("action");
        this.mapRef = useRef("map_container");
        this._map = null;

        this.state = useState({
            loading: true,
            full_count: 0,
            partial_count: 0,
            none_count: 0,
        });

        onMounted(async () => {
            await this._loadLeaflet();
            await this._loadAndRender();
        });

        onWillUnmount(() => {
            if (this._map) { this._map.remove(); this._map = null; }
        });
    }

    goBack() {
        this.actionService.doAction({ type: "ir.actions.act_window_close" });
    }

    async _loadLeaflet() {
        if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = LEAFLET_CSS;
            document.head.appendChild(link);
        }
        if (!window.L) {
            await new Promise((resolve, reject) => {
                const script = document.createElement("script");
                script.src = LEAFLET_JS;
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
        }
    }

    async _loadAndRender() {
        const { employee_id, date_from, date_to } = this.props.action.params;
        const data = await rpc("/web/dataset/call_kw", {
            model: "hr.checkpoint.log",
            method: "get_period_map_data",
            args: [employee_id, date_from, date_to],
            kwargs: {},
        });

        this.state.full_count    = data.filter(d => d.status === 'full').length;
        this.state.partial_count = data.filter(d => d.status === 'partial').length;
        this.state.none_count    = data.filter(d => d.status === 'none').length;
        this.state.loading = false;

        await new Promise(r => setTimeout(r, 50));
        this._initMap(data);
    }

    _initMap(checkpoints) {
        const el = this.mapRef.el;
        if (!el || !window.L) return;
        const L = window.L;

        const first = checkpoints.find(c => c.lat && c.lng);
        const center = first ? [first.lat, first.lng] : [DEFAULT_LAT, DEFAULT_LNG];

        this._map = L.map(el).setView(center, 13);
        L.tileLayer(OSM_TILES, {
            attribution: '© <a href="https://www.openstreetmap.org/">OpenStreetMap</a>',
            maxZoom: 19,
        }).addTo(this._map);

        for (const cp of checkpoints) {
            if (!cp.lat && !cp.lng) continue;

            const color = STATUS_COLOR[cp.status];
            const iconChar = STATUS_ICON[cp.status];

            // Cercle de périmètre
            L.circle([cp.lat, cp.lng], {
                radius: cp.radius,
                color,
                fillOpacity: 0.08,
                weight: 1,
            }).addTo(this._map);

            // Marqueur coloré
            const icon = L.divIcon({
                className: '',
                html: `<div style="
                    background:${color}; color:#fff;
                    border-radius:50%; width:34px; height:34px;
                    display:flex; align-items:center; justify-content:center;
                    font-size:15px; font-weight:bold; border:2px solid #fff;
                    box-shadow:0 2px 6px rgba(0,0,0,.35);">
                    ${iconChar}
                </div>`,
                iconSize: [34, 34],
                iconAnchor: [17, 17],
            });

            const progressBar = `
                <div style="background:#eee;border-radius:4px;height:6px;margin-top:6px;">
                    <div style="background:${color};width:${Math.min(100, Math.round(cp.visits_done/cp.visits_required*100))}%;height:6px;border-radius:4px;"></div>
                </div>`;

            const popup = `
                <strong>${cp.name}</strong><br>
                Visites : <b>${cp.visits_done}/${cp.visits_required}</b>
                ${progressBar}
                ${cp.last_visit ? `<br><small>Dernière visite : ${cp.last_visit}</small>` : ''}
            `;

            L.marker([cp.lat, cp.lng], { icon })
                .bindPopup(popup)
                .addTo(this._map);

            // Point GPS de la dernière visite
            if (cp.last_lat && cp.last_lng) {
                L.circleMarker([cp.last_lat, cp.last_lng], {
                    radius: 5,
                    color: '#0d6efd',
                    fillOpacity: 0.9,
                }).bindTooltip('Dernière position GPS').addTo(this._map);
            }
        }

        // Ajuster le zoom
        const bounds = checkpoints.filter(c => c.lat && c.lng).map(c => [c.lat, c.lng]);
        if (bounds.length > 0) this._map.fitBounds(bounds, { padding: [40, 40] });
    }
}

registry.category("actions").add("face_attendance.checkpoint_day_map", CheckpointDayMap);

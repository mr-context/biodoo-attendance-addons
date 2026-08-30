/** @odoo-module **/
/**
 * Widget carte Leaflet interactive pour configurer le géofence d'un employé.
 * Clic sur la carte → déplace le centre. Le cercle suit le champ geofence_radius.
 */
import { Component, useRef, onMounted, onWillUnmount, onPatched } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const OSM_TILES   = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const DEFAULT_LAT = 36.7372;   // Alger
const DEFAULT_LNG = 3.0869;

class GeofenceMapWidget extends Component {
    static template = "hr_face_attendance.GeofenceMap";
    static props = { ...standardWidgetProps };

    setup() {
        this.mapRef = useRef("map_container");
        this._map    = null;
        this._marker = null;
        this._circle = null;

        onMounted(async () => {
            await this._loadLeaflet();
            this._initMap();
        });

        onPatched(() => {
            if (this._circle) {
                this._circle.setRadius(this._radius());
            }
        });

        onWillUnmount(() => {
            if (this._map) {
                this._map.remove();
                this._map = null;
            }
        });
    }

    _lat()    { return this.props.record.data.geofence_lat || DEFAULT_LAT; }
    _lng()    { return this.props.record.data.geofence_lng || DEFAULT_LNG; }
    _radius() { return this.props.record.data.geofence_radius || 500; }

    async _loadLeaflet() {
        if (window.L) return;
        // CSS
        if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
            const link = document.createElement("link");
            link.rel = "stylesheet";
            link.href = LEAFLET_CSS;
            document.head.appendChild(link);
        }
        // JS
        await new Promise((resolve, reject) => {
            if (window.L) { resolve(); return; }
            const script = document.createElement("script");
            script.src = LEAFLET_JS;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    _initMap() {
        const el = this.mapRef.el;
        if (!el || !window.L) return;

        const L   = window.L;
        const lat = this._lat();
        const lng = this._lng();
        const r   = this._radius();

        this._map = L.map(el).setView([lat, lng], 15);

        L.tileLayer(OSM_TILES, {
            attribution: '© <a href="https://www.openstreetmap.org/">OpenStreetMap</a>',
            maxZoom: 19,
        }).addTo(this._map);

        this._marker = L.marker([lat, lng], { draggable: true }).addTo(this._map);
        this._circle = L.circle([lat, lng], {
            radius: r,
            color: "#0d6efd",
            fillOpacity: 0.12,
        }).addTo(this._map);

        // Clic sur la carte → déplace le centre
        this._map.on("click", (e) => this._setPosition(e.latlng.lat, e.latlng.lng));

        // Drag du marqueur → met à jour aussi
        this._marker.on("dragend", (e) => {
            const pos = e.target.getLatLng();
            this._setPosition(pos.lat, pos.lng);
        });
    }

    _setPosition(lat, lng) {
        this._marker.setLatLng([lat, lng]);
        this._circle.setLatLng([lat, lng]);
        this.props.record.update({ geofence_lat: lat, geofence_lng: lng });
    }
}

registry.category("view_widgets").add("geofence_map", {
    component: GeofenceMapWidget,
});

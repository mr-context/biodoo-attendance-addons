/** @odoo-module **/
/**
 * Client Action — Détection GPS pour configurer un lieu de travail.
 * Lit la position du navigateur et met à jour latitude/longitude sur le lieu de travail.
 */
import { Component, useState, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";

class DetectLocationAction extends Component {
    static template = "hr_face_attendance.DetectLocation";
    static props = ["action", "actionStack?"];

    setup() {
        this.notification = useService("notification");
        this.action = useService("action");

        this.state = useState({
            status: "idle",   // idle | detecting | success | error
            lat: null,
            lon: null,
            accuracy: null,
            message: "",
        });

        onMounted(() => this._detect());
    }

    async _detect() {
        if (!navigator.geolocation) {
            this.state.status = "error";
            this.state.message = "Géolocalisation non supportée par ce navigateur.";
            return;
        }
        this.state.status = "detecting";
        navigator.geolocation.getCurrentPosition(
            (pos) => this._onSuccess(pos),
            (err) => this._onError(err),
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
        );
    }

    _onSuccess(pos) {
        this.state.lat      = pos.coords.latitude;
        this.state.lon      = pos.coords.longitude;
        this.state.accuracy = Math.round(pos.coords.accuracy);
        this.state.status   = "success";
    }

    _onError(err) {
        this.state.status  = "error";
        this.state.message = {
            1: "Accès à la géolocalisation refusé. Autorisez l'accès dans les paramètres du navigateur.",
            2: "Position indisponible. Vérifiez que le GPS est activé.",
            3: "Délai de localisation dépassé. Réessayez.",
        }[err.code] || err.message;
    }

    async _save() {
        const params = this.props.action.params;
        if (params.checkpoint_id) {
            await rpc("/web/dataset/call_kw", {
                model: "hr.employee.checkpoint",
                method: "write",
                args: [[params.checkpoint_id], { geofence_lat: this.state.lat, geofence_lng: this.state.lon }],
                kwargs: {},
            });
            this.notification.add("Coordonnées enregistrées avec succès.", { type: "success" });
            this.action.doAction({ type: "ir.actions.act_window_close" });
        } else if (params.employee_id) {
            await rpc("/web/dataset/call_kw", {
                model: "hr.employee",
                method: "write",
                args: [[params.employee_id], { geofence_lat: this.state.lat, geofence_lng: this.state.lon }],
                kwargs: {},
            });
            this.notification.add("Coordonnées enregistrées avec succès.", { type: "success" });
            await this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "hr.employee",
                res_id: params.employee_id,
                views: [[false, "form"]],
                target: "current",
            });
        } else {
            await rpc("/web/dataset/call_kw", {
                model: "hr.work.location",
                method: "write",
                args: [[params.work_location_id], { latitude: this.state.lat, longitude: this.state.lon }],
                kwargs: {},
            });
            this.notification.add("Coordonnées enregistrées avec succès.", { type: "success" });
            this.action.doAction({ type: "ir.actions.act_window_close" });
        }
    }

    _retry() { this._detect(); }
}

registry.category("actions").add("face_attendance.detect_location", DetectLocationAction);

/** @odoo-module **/
import { Component, useState, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { Dialog } from "@web/core/dialog/dialog";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const ENROLL_CHANNEL = "zkteco_enroll";
const ENROLL_TYPE = "zkteco_enroll_result";
const DEFAULT_TIMEOUT = 45; // secondes avant de déclarer "pas de réponse"

/**
 * Moniteur d'enrôlement empreinte : empreinte animée qui "scanne", écoute le
 * bus, et bascule en succès / échec dès que le device renvoie le template.
 */
export class FingerprintEnrollDialog extends Component {
    static template = "zkteco_connector.FingerprintEnrollDialog";
    static components = { Dialog };
    static props = {
        close: Function,
        pin: { type: [String, Number] },
        fid: { type: [String, Number], optional: true },
        bioType: { type: [String, Number], optional: true },
        fingerLabel: { type: String, optional: true },
        employeeName: { type: String, optional: true },
        deviceNames: { type: Array, optional: true },
        timeout: { type: Number, optional: true },
    };

    setup() {
        this.bus = useService("bus_service");
        this.action = useService("action");
        this.state = useState({
            status: "waiting", // waiting | success | failed | timeout
            message: "",
            elapsed: 0,
        });

        this._fid = this.props.fid === undefined ? null : parseInt(this.props.fid, 10);
        this._bioType = parseInt(this.props.bioType ?? 1, 10);
        this._timeoutSec = this.props.timeout || DEFAULT_TIMEOUT;

        this._onResult = this._onResult.bind(this);
        this.bus.addChannel(ENROLL_CHANNEL);
        this.bus.subscribe(ENROLL_TYPE, this._onResult);

        this._tick = setInterval(() => {
            this.state.elapsed += 1;
            if (this.state.status === "waiting" && this.state.elapsed >= this._timeoutSec) {
                this.state.status = "timeout";
                this._cleanup();
            }
        }, 1000);

        onWillUnmount(() => this._cleanup());
    }

    get devicesLabel() {
        return (this.props.deviceNames || []).join(", ");
    }

    get fingerName() {
        return this.state.message || this.props.fingerLabel || "";
    }

    _matches(p) {
        if (String(p.pin) !== String(this.props.pin)) {
            return false;
        }
        const ptype = parseInt(p.bio_type ?? 0, 10);
        if (this._bioType && ptype !== this._bioType) {
            return false;
        }
        // Pour une empreinte, on attend le bon doigt.
        if (this._fid !== null && ptype === 1 && parseInt(p.finger_id ?? -1, 10) !== this._fid) {
            return false;
        }
        return true;
    }

    _onResult(payload) {
        if (this.state.status !== "waiting" || !this._matches(payload)) {
            return;
        }
        if (payload.valid) {
            this.state.status = "success";
            this.state.message = payload.finger_label || this.props.fingerLabel || "";
        } else {
            this.state.status = "failed";
            this.state.message = _t("Template rejeté par la pointeuse. Réessayez l'enrôlement.");
        }
        this._cleanup();
    }

    _cleanup() {
        if (this._tick) {
            clearInterval(this._tick);
            this._tick = null;
        }
        try {
            this.bus.unsubscribe(ENROLL_TYPE, this._onResult);
        } catch {
            // service déjà nettoyé
        }
    }

    close() {
        this.props.close();
    }

    closeAndReload() {
        this.props.close();
        this.action.doAction({ type: "ir.actions.client", tag: "soft_reload" });
    }
}

// Action client retournée par le wizard d'enrôlement empreinte.
registry.category("actions").add("zkteco_enroll_monitor", (env, action) => {
    env.services.dialog.add(FingerprintEnrollDialog, { ...action.params });
});
/** @odoo-module **/
//
// A — rafraîchissement LIVE de l'indicateur en ligne/hors ligne des pointeuses.
// Le backend pousse une notif bus « zkteco_device_status » UNIQUEMENT quand le
// statut d'un device bascule (cron hors ligne / heartbeat retour en ligne). On
// patche les contrôleurs kanban & liste : dès qu'ils affichent le modèle
// zkteco.device, ils s'abonnent au canal et rechargent (debounce) à la notif —
// plus besoin de recharger la page à la main. Coût : ~0 (recharge seulement sur
// transition, jamais sur les heartbeats).

import { patch } from "@web/core/utils/patch";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";
import { useService } from "@web/core/utils/hooks";
import { onWillUnmount } from "@odoo/owl";

const CHANNEL = "zkteco_device_status";
const NOTIF_TYPE = "zkteco_device_status";

function installDeviceStatusLiveRefresh() {
    if (this.props.resModel !== "zkteco.device") {
        return;
    }
    const bus = useService("bus_service");
    let timer = null;
    const reload = () => {
        // Plusieurs devices peuvent basculer en même temps → un seul rechargement.
        clearTimeout(timer);
        timer = setTimeout(() => {
            const root = this.model && this.model.root;
            if (root && typeof root.load === "function") {
                root.load();
            }
        }, 400);
    };
    bus.addChannel(CHANNEL);
    bus.subscribe(NOTIF_TYPE, reload);
    onWillUnmount(() => {
        clearTimeout(timer);
        bus.unsubscribe(NOTIF_TYPE, reload);
    });
}

patch(KanbanController.prototype, {
    setup() {
        super.setup(...arguments);
        installDeviceStatusLiveRefresh.call(this);
    },
});

patch(ListController.prototype, {
    setup() {
        super.setup(...arguments);
        installDeviceStatusLiveRefresh.call(this);
    },
});

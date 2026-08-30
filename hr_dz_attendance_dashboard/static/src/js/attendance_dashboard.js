/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { listView } from "@web/views/list/list_view";
import { kanbanView } from "@web/views/kanban/kanban_view";
import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { AttendanceListRenderer } from "@hr_attendance/views/attendance_list_view";

// =============================================================================
// Préférence utilisateur (repli/dépli), en localStorage — propre à ce
// navigateur, pas une donnée métier à stocker en base.
// =============================================================================

const COLLAPSE_STORAGE_KEY = "biodoo_attendance_dashboard_collapsed";

function readCollapsedPref() {
    try {
        return localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1";
    } catch {
        return false;
    }
}

function writeCollapsedPref(value) {
    try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, value ? "1" : "0");
    } catch {
        // Stockage indisponible (navigation privée...) : la préférence ne
        // survivra juste pas à la session, tant pis.
    }
}

// =============================================================================
// Épinglage par glisser-déposer — un utilisateur qui veut surveiller une
// pointeuse ou un calendrier précis peut le sortir de la liste déroulante
// pour le garder affiché en permanence (et l'inverse pour le ranger).
// Préférence par navigateur, comme le repli du bandeau.
// =============================================================================

const PIN_STORAGE_KEYS = {
    device: "biodoo_attendance_pinned_devices",
    calendar: "biodoo_attendance_pinned_calendars",
};

function readPinnedIds(kind) {
    try {
        const raw = localStorage.getItem(PIN_STORAGE_KEYS[kind]);
        return new Set(raw ? JSON.parse(raw) : []);
    } catch {
        return new Set();
    }
}

function writePinnedIds(kind, idSet) {
    try {
        localStorage.setItem(PIN_STORAGE_KEYS[kind], JSON.stringify([...idSet]));
    } catch {
        // Stockage indisponible : l'épinglage ne survivra pas à la session.
    }
}

// =============================================================================
// Chargement des statistiques (endpoint JSON, recalculé à chaque appel —
// aucun champ stocké côté serveur, voir controllers/dashboard.py).
// =============================================================================

function initDashboardState() {
    return {
        loaded: false,
        authorized: true,
        date: new Date().toISOString().slice(0, 10),

        contract_count: 0,
        present_count: 0,
        absent_count: 0,
        late_count: 0,
        early_count: 0,
        open_count: 0,
        leave_count: 0,
        pending_deductions: 0,
        break_issue_count: 0,
        trial_alert_count: 0,
        cdd_alert_count: 0,

        present_attendance_ids: [],
        absent_employee_ids: [],
        late_attendance_ids: [],
        early_attendance_ids: [],
        open_attendance_ids: [],
        leave_ids: [],
        deduction_ids: [],
        break_issue_ids: [],
        trial_alert_ids: [],
        cdd_alert_ids: [],

        device_status: [],
        device_online_count: 0,
        calendar_breakdown: [],
    };
}

async function loadDashboardStats(state, dateStr) {
    const result = await rpc("/biodoo_attendance/dashboard_stats", {
        date_str: dateStr || null,
    });
    Object.assign(state, result, { loaded: true });
}

// =============================================================================
// Config des cartes KPI — une seule source de vérité pour le template
// (t-foreach) au lieu de dupliquer le même bloc HTML huit fois.
// =============================================================================

const STAT_CARDS = [
    { key: "present", icon: "fa-check", color: "o_biodoo_c_purple", label: "Présents", valueKey: "present_count" },
    { key: "absent", icon: "fa-user-times", color: "o_biodoo_c_danger", label: "Absents", valueKey: "absent_count" },
    { key: "late", icon: "fa-clock-o", color: "o_biodoo_c_warning", label: "Retards", valueKey: "late_count" },
    { key: "early", icon: "fa-sign-out", color: "o_biodoo_c_warning", label: "Départs anticipés", valueKey: "early_count" },
    { key: "open", icon: "fa-hourglass-half", color: "o_biodoo_c_teal", label: "Encore présents", valueKey: "open_count" },
    { key: "break_issue", icon: "fa-coffee", color: "o_biodoo_c_warning", label: "Pauses non conformes", valueKey: "break_issue_count" },
    { key: "leave", icon: "fa-plane", color: "o_biodoo_c_muted", label: "En congé", valueKey: "leave_count" },
    { key: "deduction", icon: "fa-exclamation-triangle", color: "o_biodoo_c_muted", label: "Déductions à valider", valueKey: "pending_deductions" },
];

// Alertes contrats : toujours "à ce jour", indépendantes de la date choisie
// dans le bandeau — affichées séparément, seulement si non nulles.
const ALERT_CARDS = [
    { key: "trial_alert", icon: "fa-user-circle-o", label: "Périodes d'essai à risque", valueKey: "trial_alert_count" },
    { key: "cdd_alert", icon: "fa-file-text-o", label: "Fins de CDD ≤ 30 jours", valueKey: "cdd_alert_count" },
];

const DRILL_DOWN = {
    present: {
        resModel: "hr.attendance",
        idsKey: "present_attendance_ids",
        name: "Présences du jour",
        views: [[false, "list"], [false, "form"]],
        // Un employé peut avoir plusieurs segments le même jour (horaire
        // "punch state" désactivé → pause déduite automatiquement, matin/
        // après-midi = 2 lignes hr.attendance). Grouper par employé évite
        // une liste à plat qui semble dupliquée alors que le badge, lui,
        // compte des employés distincts.
        context: { group_by: ["employee_id"] },
    },
    absent: {
        resModel: "hr.employee",
        idsKey: "absent_employee_ids",
        name: "Absents du jour",
        views: [[false, "list"], [false, "form"]],
    },
    late: {
        resModel: "hr.attendance",
        idsKey: "late_attendance_ids",
        name: "Retards du jour",
        views: [[false, "list"], [false, "form"]],
    },
    early: {
        resModel: "hr.attendance",
        idsKey: "early_attendance_ids",
        name: "Départs anticipés du jour",
        views: [[false, "list"], [false, "form"]],
    },
    open: {
        resModel: "hr.attendance",
        idsKey: "open_attendance_ids",
        name: "Encore présents",
        views: [[false, "list"], [false, "form"]],
    },
    break_issue: {
        resModel: "zkteco.attendance.break",
        idsKey: "break_issue_ids",
        name: "Pauses non conformes du jour",
        views: [[false, "list"], [false, "form"]],
    },
    leave: {
        resModel: "hr.leave",
        idsKey: "leave_ids",
        name: "En congé",
        views: [[false, "list"], [false, "form"]],
    },
    deduction: {
        resModel: "hr.attendance.deduction",
        idsKey: "deduction_ids",
        name: "Déductions à valider",
        views: [[false, "list"], [false, "form"]],
    },
    trial_alert: {
        resModel: "hr.version",
        idsKey: "trial_alert_ids",
        name: "Périodes d'essai à risque",
        views: [[false, "list"], [false, "form"]],
        dateScoped: false,
    },
    cdd_alert: {
        resModel: "hr.version",
        idsKey: "cdd_alert_ids",
        name: "Fins de CDD à moins de 30 jours",
        views: [[false, "list"], [false, "form"]],
        dateScoped: false,
    },
};

// Taille du pack de pointeuses le plus vendu aux clients : en dessous ou
// égal, on affiche le détail de chacune directement dans le bandeau ; au
// delà, ça deviendrait illisible → pastille compacte + liste au clic.
const DEVICE_EXPANDED_THRESHOLD = 5;

// Même logique pour les calendriers/shifts : jusqu'à 10 affichés en
// puces directement, au-delà ça part en pastille compacte + liste.
const CALENDAR_EXPANDED_THRESHOLD = 10;

// =============================================================================
// Mixin partagé par les Renderers liste et kanban de hr.attendance.
// =============================================================================

const DashboardMixin = (Base) =>
    class extends Base {
        setup() {
            super.setup();
            this.dashboard = useState(initDashboardState());
            this.uiState = useState({ collapsed: readCollapsedPref() });
            this.deviceUi = useState({ open: false });
            this.calendarUi = useState({ open: false });
            this.pinned = useState({
                device: readPinnedIds("device"),
                calendar: readPinnedIds("calendar"),
            });
            // Zone actuellement survolée pendant un glisser — pour afficher
            // une ombre de destination (sinon on glisse "à l'aveugle").
            this.dragState = useState({ overZone: null });
            this.actionService = useService("action");
            this.notification = useService("notification");
            onWillStart(() => loadDashboardStats(this.dashboard));
        }

        // ── Épinglage générique (glisser-déposer) ─────────────────────────
        onDragStart(ev, kind, id) {
            ev.dataTransfer.setData("text/plain", JSON.stringify({ kind, id }));
            ev.dataTransfer.effectAllowed = "move";
        }

        onDragEnd() {
            // Filet de sécurité : couvre le cas où le glisser est annulé
            // (relâché hors de toute zone) sans passer par un "drop".
            this.dragState.overZone = null;
        }

        isZoneActive(zone) {
            return this.dragState.overZone === zone;
        }

        onDragOverZone(ev, zone) {
            ev.preventDefault();
            if (this.dragState.overZone !== zone) {
                this.dragState.overZone = zone;
            }
        }

        onDragLeaveZone(zone) {
            if (this.dragState.overZone === zone) {
                this.dragState.overZone = null;
            }
        }

        onDropOnPinZone(ev, kind) {
            ev.preventDefault();
            this.dragState.overZone = null;
            const { kind: draggedKind, id } = JSON.parse(ev.dataTransfer.getData("text/plain"));
            if (draggedKind !== kind) return;
            this.pinned[kind].add(id);
            writePinnedIds(kind, this.pinned[kind]);
        }

        onDropOnListZone(ev, kind) {
            ev.preventDefault();
            this.dragState.overZone = null;
            const { kind: draggedKind, id } = JSON.parse(ev.dataTransfer.getData("text/plain"));
            if (draggedKind !== kind) return;
            this.pinned[kind].delete(id);
            writePinnedIds(kind, this.pinned[kind]);
        }

        get statCards() {
            return STAT_CARDS;
        }

        get alertCards() {
            return ALERT_CARDS.filter((card) => this.dashboard[card.valueKey] > 0);
        }

        // ── Repli / dépli du bandeau ─────────────────────────────────────
        onToggleDashboard() {
            this.uiState.collapsed = !this.uiState.collapsed;
            writeCollapsedPref(this.uiState.collapsed);
        }

        // ── Navigation dans le temps ─────────────────────────────────────
        get dashboardDayLabel() {
            const d = new Date(`${this.dashboard.date}T00:00:00`);
            const label = new Intl.DateTimeFormat("fr-FR", { weekday: "long" }).format(d);
            return label.charAt(0).toUpperCase() + label.slice(1);
        }

        get presenceRate() {
            if (!this.dashboard.contract_count) {
                return 0;
            }
            return Math.round(
                (this.dashboard.present_count / this.dashboard.contract_count) * 100
            );
        }

        onDashboardDateChange(ev) {
            loadDashboardStats(this.dashboard, ev.target.value);
        }

        onDashboardToday() {
            loadDashboardStats(this.dashboard, null);
        }

        onDashboardShiftDay(offset) {
            const current = new Date(`${this.dashboard.date}T00:00:00`);
            current.setDate(current.getDate() + offset);
            loadDashboardStats(this.dashboard, current.toISOString().slice(0, 10));
        }

        // ── Drill-down au clic sur une carte KPI / alerte ────────────────
        onDashboardCardClick(key) {
            const conf = DRILL_DOWN[key];
            const ids = this.dashboard[conf.idsKey] || [];
            if (!ids.length) {
                this.notification.add("Aucun enregistrement pour ce filtre.", { type: "info" });
                return;
            }
            const dateScoped = conf.dateScoped !== false;
            this.actionService.doAction({
                type: "ir.actions.act_window",
                name: dateScoped ? `${conf.name} — ${this.dashboard.date}` : conf.name,
                res_model: conf.resModel,
                domain: [["id", "in", ids]],
                views: conf.views,
                context: conf.context || {},
                target: "current",
            });
        }

        // ── Répartition par calendrier ────────────────────────────────────
        get pinnedCalendars() {
            return this.dashboard.calendar_breakdown.filter(
                (entry) => this.pinned.calendar.has(entry.calendar_id));
        }

        get unpinnedCalendars() {
            return this.dashboard.calendar_breakdown.filter(
                (entry) => !this.pinned.calendar.has(entry.calendar_id));
        }

        // Le mode (détail direct vs. pastille+liste) dépend du total
        // D'ORIGINE, pas du nombre restant après épinglage — sinon épingler
        // un seul élément sur 11 ferait brusquement basculer les 10 autres
        // en affichage détaillé, ce qui n'a pas de sens pour l'utilisateur.
        get unpinnedCalendarsExpanded() {
            return this.unpinnedCalendars.length > 0
                && this.dashboard.calendar_breakdown.length <= CALENDAR_EXPANDED_THRESHOLD;
        }

        onToggleCalendars() {
            this.calendarUi.open = !this.calendarUi.open;
        }

        onCalendarClick(entry) {
            if (!entry.employee_ids.length) {
                this.notification.add("Aucun employé sur ce calendrier.", { type: "info" });
                return;
            }
            this.actionService.doAction({
                type: "ir.actions.act_window",
                name: `${entry.calendar_name} — ${this.dashboard.date}`,
                res_model: "hr.employee",
                domain: [["id", "in", entry.employee_ids]],
                views: [[false, "list"], [false, "form"]],
                target: "current",
            });
        }

        // ── Statut des pointeuses ─────────────────────────────────────────
        get pinnedDevices() {
            return this.dashboard.device_status.filter((dev) => this.pinned.device.has(dev.id));
        }

        get unpinnedDevices() {
            return this.dashboard.device_status.filter((dev) => !this.pinned.device.has(dev.id));
        }

        // Même principe : basé sur le total d'origine, pas le nombre
        // restant, pour que le mode ne change pas au fil de l'épinglage.
        get unpinnedDevicesExpanded() {
            return this.unpinnedDevices.length > 0
                && this.dashboard.device_status.length <= DEVICE_EXPANDED_THRESHOLD;
        }

        deviceLastSeenLabel(device) {
            if (!device.last_seen) {
                return "jamais vue";
            }
            const diffMin = Math.round((Date.now() - new Date(device.last_seen).getTime()) / 60000);
            if (diffMin < 1) return "à l'instant";
            if (diffMin < 60) return `il y a ${diffMin} min`;
            const diffH = Math.round(diffMin / 60);
            if (diffH < 24) return `il y a ${diffH} h`;
            return `il y a ${Math.round(diffH / 24)} j`;
        }

        onToggleDevices() {
            this.deviceUi.open = !this.deviceUi.open;
        }

        onDeviceClick(device) {
            this.actionService.doAction({
                type: "ir.actions.act_window",
                name: device.name,
                res_model: "zkteco.device",
                res_id: device.id,
                views: [[false, "form"]],
                target: "current",
            });
        }
    };

// =============================================================================
// Enregistrement des vues (list / kanban) surchargées
// =============================================================================

export class BiodooAttendanceListRenderer extends DashboardMixin(AttendanceListRenderer) {
    static template = "hr_dz_attendance_dashboard.AttendanceListRenderer";
}

export const biodooAttendanceListView = {
    ...listView,
    Renderer: BiodooAttendanceListRenderer,
};

registry.category("views").add("biodoo_attendance_list_view", biodooAttendanceListView);

export class BiodooAttendanceKanbanRenderer extends DashboardMixin(KanbanRenderer) {
    static template = "hr_dz_attendance_dashboard.AttendanceKanbanRenderer";
}

export const biodooAttendanceKanbanView = {
    ...kanbanView,
    Renderer: BiodooAttendanceKanbanRenderer,
};

registry.category("views").add("biodoo_attendance_kanban_view", biodooAttendanceKanbanView);

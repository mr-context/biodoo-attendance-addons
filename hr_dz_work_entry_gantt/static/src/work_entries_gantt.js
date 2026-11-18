import { markup, onWillStart, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { serializeDate } from "@web/core/l10n/dates";
import { registry } from "@web/core/registry";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";
import { escape } from "@web/core/utils/strings";
import { formatFloatTime } from "@web/views/fields/formatters";
import { hrGanttView } from "@hr_dz_gantt/hr_gantt_view";
import { HrGanttRenderer } from "@hr_dz_gantt/hr_gantt_renderer";
import { shortHours } from "@hr_dz_gantt/hr_gantt_helpers";

/** Types proposés d'abord pour saisir une journée (le reste : « Autre type »). */
const COMMON_CODES = ["WORK100", "CA", "MALADIE", "RECUP", "ABSENT", "CSS", "FERIE"];

/** Type de la présence normale : affiché en neutre, pour que les écarts ressortent. */
const NORMAL_CODE = "WORK100";

/** Au-delà de ce nombre de journées, l'action de masse est toujours confirmée. */
const CONFIRM_ABOVE = 20;

/**
 * Prestations de paie en Gantt.
 *
 * Lecture : la présence normale est neutre et pâle, chaque écart (heures sup.,
 * congé, maladie, absence, retard…) a sa couleur franche ; le total d'heures
 * de chaque employé est en tête de ligne.
 *
 * Correction : on glisse sur des journées (un ou plusieurs employés), puis on
 * choisit le type qui les remplace, ou on les rend au calcul automatique.
 * Avant d'écrire : aperçu côté serveur, confirmation si des heures sup. ou des
 * retenues seraient perdues (ou au-delà de 20 journées), jours non travaillés
 * exclus sauf choix explicite. Après : notification avec « Annuler » ; tout
 * est noté dans le journal (Présences → Analyse).
 */
export class WorkEntriesGanttRenderer extends HrGanttRenderer {
    static selectionBarTemplate = "hr_dz_work_entry_gantt.SelectionBar";
    static components = { ...HrGanttRenderer.components, Dropdown, DropdownItem };

    setup() {
        super.setup();
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.workEntryTypes = [];
        this.options = useState({ includeOffDays: false });
        onWillStart(async () => {
            // Une journée se saisit avec un type « journée » : pas d'heures sup.,
            // de retenue de retard, d'heures mises en récupération, ni « hors contrat ».
            this.workEntryTypes = await this.orm.searchRead(
                "hr.work.entry.type",
                [
                    ["active", "=", true],
                    ["is_extra_hours", "=", false],
                    ["overtime_rate", "<=", 1],
                    ["dz_recovery_role", "in", [false, "taken"]],
                    ["code", "not in", ["OUT", "LATE", "EARLY"]],
                ],
                ["display_name", "color", "code"],
                { order: "sequence, id" }
            );
        });
    }

    get rootClass() {
        return "o_dz_work_entries_gantt";
    }

    get selectionEnabled() {
        return true;
    }

    onSelectionDone(selection) {
        if (!selection) {
            this.interaction.selection = null;
            return;
        }
        // La sélection reste affichée : la barre d'actions la remplace dans le pied.
        this.options.includeOffDays = false;
    }

    // ── Lecture ────────────────────────────────────────────────────────────

    isNormal(pill) {
        return pill.record.code === NORMAL_CODE;
    }

    pillClass(pill) {
        const classes = super.pillClass(pill);
        return `${classes} ${this.isNormal(pill) ? "o_dz_gantt_pill_normal" : "o_dz_gantt_pill_exception"}`;
    }

    getPillLabel(pill) {
        const duration = shortHours(pill.record.duration);
        return this.metaData.scale === "week"
            ? `${pill.record.work_entry_type_id?.display_name || ""} ${duration}`.trim()
            : duration;
    }

    getPillTitle(pill) {
        const type = pill.record.work_entry_type_id?.display_name || pill.label;
        return `${type} — ${formatFloatTime(pill.record.duration || 0)}`;
    }

    getAggregateLabel(value) {
        return shortHours(value);
    }

    getLegendCountLabel(entry) {
        return _t("%(count)s j · %(hours)s", { count: entry.count, hours: shortHours(entry.total) });
    }

    get legendHint() {
        return _t("Glissez sur des journées pour les remplacer");
    }

    get rowTotalTitle() {
        return _t("Heures de l'employé sur la période affichée");
    }

    get staticLegend() {
        return [{ corner: "warning", label: _t("En conflit") }];
    }

    // ── Barre d'actions de la sélection ────────────────────────────────────

    /**
     * Boutons directs (6) : les types déjà présents sur la période d'abord,
     * puis les types courants ; le reste est dans « Autre type ».
     */
    get quickTypes() {
        const used = new Set(
            this.model.data.records.map((r) => r.work_entry_type_id?.id).filter(Boolean)
        );
        const quick = this.workEntryTypes.filter((t) => used.has(t.id));
        const rank = (t) => (COMMON_CODES.includes(t.code) ? COMMON_CODES.indexOf(t.code) : 99);
        for (const type of [...this.workEntryTypes].sort((a, b) => rank(a) - rank(b))) {
            if (quick.length >= 6) {
                break;
            }
            if (!used.has(type.id)) {
                quick.push(type);
            }
        }
        return quick.slice(0, 6);
    }

    get selectionSummary() {
        const selection = this.getSelection();
        if (!selection) {
            return "";
        }
        const days = selection.last - selection.first;
        const people = selection.rows.length;
        return people > 1
            ? _t("%(days)s jour(s) × %(people)s lignes", { days, people })
            : _t("%(days)s jour(s)", { days });
    }

    /** La sélection contient-elle des jours hors planning ? (case à cocher) */
    get selectionHasOffDays() {
        const selection = this.getSelection();
        if (!selection) {
            return false;
        }
        return selection.rows.some((row) => {
            for (let i = selection.first; i < selection.last; i++) {
                if (row.unavailable?.has(i)) {
                    return true;
                }
            }
            return false;
        });
    }

    /** Cases → [[employee_id, "AAAA-MM-JJ"], …] ; null si une ligne n'a pas d'employé. */
    selectedCells() {
        const selection = this.getSelection();
        if (!selection) {
            return [];
        }
        const cells = [];
        for (const row of selection.rows) {
            const employeeId = row.values?.employee_id;
            if (!employeeId) {
                return null;
            }
            for (let i = selection.first; i < selection.last; i++) {
                cells.push([employeeId, serializeDate(this.metaData.columns[i].start)]);
            }
        }
        return cells;
    }

    confirm(title, body, confirmLabel) {
        return new Promise((resolve) => {
            this.dialog.add(ConfirmationDialog, {
                title, body, confirmLabel,
                confirm: () => resolve(true),
                cancel: () => resolve(false),
            });
        });
    }

    async onApplyType(type) {
        const cells = this.selectedCells();
        if (!cells) {
            return this.warnNeedEmployees();
        }
        const includeOffDays = this.options.includeOffDays;
        const preview = await this.orm.call("hr.work.entry", "dz_gantt_preview", [cells, includeOffDays]);
        if (!preview.todo) {
            this.notification.add(this.skippedMessage(preview) || _t("Rien à modifier."), { type: "warning" });
            return;
        }
        if (preview.sensitive || preview.todo > CONFIRM_ABOVE) {
            const lines = [_t("%(n)s journée(s) remplacée(s) par « %(type)s ».", {
                n: preview.todo, type: type.display_name })];
            if (preview.sensitive) {
                lines.push(_t("Attention : %s journée(s) contiennent des heures supplémentaires ou des retenues calculées, qui seront perdues.", preview.sensitive));
            }
            const skipped = this.skippedMessage(preview);
            if (skipped) {
                lines.push(skipped);
            }
            lines.push(_t("Vous pourrez annuler juste après, ou depuis le journal."));
            const body = markup(lines.map((line) => escape(line)).join("<br/>"));
            if (!(await this.confirm(_t("Remplacer ces journées ?"), body, _t("Remplacer")))) {
                return;
            }
        }
        const result = await this.orm.call("hr.work.entry", "dz_gantt_set_type",
                                           [cells, type.id, includeOffDays]);
        await this.afterChange(result, _t("%(n)s journée(s) → « %(type)s »", {
            n: result.done, type: type.display_name }));
    }

    async onReset() {
        const cells = this.selectedCells();
        if (!cells) {
            return this.warnNeedEmployees();
        }
        if (cells.length > CONFIRM_ABOVE && !(await this.confirm(
            _t("Rendre au calcul automatique ?"),
            _t("Les saisies RH de %s journée(s) seront retirées et recalculées depuis les pointages.", cells.length),
            _t("Recalculer")))) {
            return;
        }
        const result = await this.orm.call("hr.work.entry", "dz_gantt_reset", [cells]);
        await this.afterChange(result, _t("%s journée(s) rendue(s) au calcul automatique", result.done));
    }

    skippedMessage(result) {
        const parts = [];
        if (result.off) {
            parts.push(_t("%s jour(s) non travaillé(s) laissé(s) de côté", result.off));
        }
        if (result.locked) {
            parts.push(_t("%s jour(s) verrouillé(s) (paie validée ou hors contrat)", result.locked));
        }
        return parts.join(" ; ");
    }

    async afterChange(result, message) {
        this.interaction.selection = null;
        await this.model.reload();
        const skipped = this.skippedMessage(result);
        const buttons = result.change_id
            ? [{ name: _t("Annuler"), icon: "fa-undo", primary: true,
                 onClick: () => this.undo(result.change_id) }]
            : [];
        this.notification.add(skipped ? `${message}. ${skipped}.` : message, {
            type: result.done ? "success" : "warning",
            buttons,
        });
    }

    async undo(changeId) {
        await this.orm.call("hr.work.entry", "dz_gantt_undo", [changeId]);
        await this.model.reload();
        this.notification.add(_t("Modification annulée."), { type: "info" });
    }

    warnNeedEmployees() {
        this.notification.add(_t("Regroupez les lignes par employé pour modifier des journées."), {
            type: "warning",
        });
    }
}

export const workEntriesGanttView = {
    ...hrGanttView,
    Renderer: WorkEntriesGanttRenderer,
};

registry.category("views").add("dz_work_entries_gantt", workEntriesGanttView);

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { hrGanttView } from "@hr_dz_gantt/hr_gantt_view";
import { HrGanttRenderer } from "@hr_dz_gantt/hr_gantt_renderer";
import { shortHours } from "@hr_dz_gantt/hr_gantt_helpers";

/**
 * Présences : heures d'entrée et de sortie dans la barre (jour, semaine),
 * durée en vue mois ; bulle qui dit l'essentiel — arrivée, sortie (ou « non
 * pointée » en rouge), durée, heures sup., origine du pointage.
 */
export class AttendanceGanttRenderer extends HrGanttRenderer {
    get popoverShowsPeriod() {
        return false;
    }

    modeLabel(name, value) {
        const field = this.metaData.fields[name];
        return (field?.selection || []).find(([key]) => key === value)?.[1] || value || "";
    }

    getPopoverExtraLines(pill) {
        const record = pill.record;
        const lines = [{ label: _t("Arrivée"), text: pill.start.toFormat("dd/MM/yyyy HH:mm") }];
        if (record.check_out) {
            lines.push({ label: _t("Sortie"), text: pill.stop.toFormat("dd/MM/yyyy HH:mm") });
            lines.push({ label: _t("Durée"), text: shortHours(record.worked_hours) });
            if (record.overtime_hours > 0) {
                lines.push({ label: _t("Heures sup."), text: shortHours(record.overtime_hours) });
            }
        } else {
            lines.push({ label: _t("Sortie"), text: _t("non pointée"), className: "text-danger fw-bold" });
        }
        const origin = [this.modeLabel("in_mode", record.in_mode)];
        if (record.check_out && record.out_mode && record.out_mode !== record.in_mode) {
            origin.push(this.modeLabel("out_mode", record.out_mode));
        }
        lines.push({ label: _t("Origine"), text: origin.filter(Boolean).join(" / ") });
        return lines;
    }

    getPillLabel(pill) {
        const from = pill.start.toFormat("HH:mm");
        const to = pill.record.check_out ? pill.stop.toFormat("HH:mm") : "…";
        if (this.metaData.scale === "month") {
            return pill.record.check_out ? shortHours(pill.record.worked_hours) : "…";
        }
        return `${from} – ${to}`;
    }

    getPillTitle(pill) {
        const worked = pill.record.check_out ? ` (${shortHours(pill.record.worked_hours)})` : _t(" — sortie non pointée");
        return `${pill.start.toFormat("dd/MM HH:mm")} → ${pill.record.check_out ? pill.stop.toFormat("HH:mm") : "?"}${worked}`;
    }

    /** Couleurs d'Odoo pour les présences (champ `color` de hr.attendance). */
    get staticLegend() {
        return [
            { colorIndex: 0, label: _t("Pointage complet") },
            { colorIndex: 10, label: _t("En cours aujourd'hui") },
            { colorIndex: 1, label: _t("Anomalie : sortie manquante ou plus de 16 h") },
            { corner: "danger", label: _t("Sortie non pointée") },
        ];
    }

    getPopoverTitle(pill) {
        const to = pill.record.check_out ? pill.stop.toFormat("HH:mm") : _t("sortie non pointée");
        return _t("Pointage du %(day)s : %(from)s → %(to)s", {
            day: pill.start.toFormat("dd/MM"), from: pill.start.toFormat("HH:mm"), to });
    }

    getAggregateLabel(value) {
        return shortHours(value);
    }

    get rowTotalTitle() {
        return _t("Heures pointées sur la période affichée");
    }
}

export const attendanceGanttView = {
    ...hrGanttView,
    Renderer: AttendanceGanttRenderer,
};

registry.category("views").add("dz_attendance_gantt", attendanceGanttView);

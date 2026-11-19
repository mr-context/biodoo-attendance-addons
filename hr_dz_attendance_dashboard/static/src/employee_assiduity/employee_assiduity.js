/** @odoo-module **/

// =============================================================================
// Analyse d'assiduité d'un employé (bouton « Assiduité » de la fiche employé).
//
// Tout vient de l'endpoint /biodoo_attendance/employee_assiduity, qui LIT ce
// que biodoo a déjà établi (moteur de prestations day_facts, anomalies,
// retenues) — voir models/assiduity_analysis.py. Ici : affichage seulement.
// =============================================================================

import { Component, onWillStart, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { rpc } from "@web/core/network/rpc";
import { loadBundle } from "@web/core/assets";
import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";

const { DateTime, Info } = luxon;

// Palette alignée sur attendance_dashboard.scss.
const COLORS = {
    purple: "#714B67",
    teal: "#017e84",
    warning: "#e99d00",
    danger: "#d44c59",
    muted: "#7c7f89",
};

const PRESETS = [
    { key: "month", label: "Ce mois" },
    { key: "last_month", label: "Mois dernier" },
    { key: "quarter", label: "3 derniers mois" },
    { key: "year", label: "Cette année" },
    { key: "last12", label: "12 derniers mois" },
    { key: "custom", label: "Personnalisé" },
];

const LEVEL_LABELS = {
    good: "Bon",
    warning: "À surveiller",
    danger: "Insuffisant",
    none: "Pas de donnée",
};

function presetRange(key) {
    const today = DateTime.local().startOf("day");
    switch (key) {
        case "last_month": {
            const m = today.minus({ months: 1 });
            return [m.startOf("month"), m.endOf("month").startOf("day")];
        }
        case "quarter":
            return [today.minus({ months: 2 }).startOf("month"), today];
        case "year":
            return [today.startOf("year"), today];
        case "last12":
            return [today.minus({ months: 11 }).startOf("month"), today];
        default:
            return [today.startOf("month"), today];
    }
}

const iso = (dt) => dt.toISODate();

export class EmployeeAssiduity extends Component {
    static template = "hr_dz_attendance_dashboard.EmployeeAssiduity";
    static props = ["*"];

    setup() {
        this.actionService = useService("action");
        this.orm = useService("orm");
        this.notification = useService("notification");
        const params = this.props.action.params || {};
        this.employeeId = params.employee_id || this.props.action.context?.active_id;
        const [from, to] = presetRange("month");
        this.state = useState({
            loading: true,
            data: null,
            preset: "month",
            dateFrom: iso(from),
            dateTo: iso(to),
            onlyIssues: true,
        });
        this.trendRef = useRef("trendChart");
        this.weekdayRef = useRef("weekdayChart");
        this.arrivalRef = useRef("arrivalChart");
        this.charts = [];

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        useEffect(
            () => {
                this.renderCharts();
            },
            () => [this.state.data]
        );
        onWillUnmount(() => this.destroyCharts());
    }

    // ── Données ───────────────────────────────────────────────────────────
    async load() {
        if (!this.employeeId) {
            this.state.loading = false;
            return;
        }
        this.state.loading = true;
        const data = await rpc("/biodoo_attendance/employee_assiduity", {
            employee_id: this.employeeId,
            date_from: this.state.dateFrom,
            date_to: this.state.dateTo,
        });
        if (data.period) {
            this.state.dateFrom = data.period.date_from;
            this.state.dateTo = data.period.date_to;
        }
        this.state.data = data;
        this.state.loading = false;
    }

    get presets() {
        return PRESETS;
    }

    onPresetChange(ev) {
        this.state.preset = ev.target.value;
        if (this.state.preset !== "custom") {
            const [from, to] = presetRange(this.state.preset);
            this.state.dateFrom = iso(from);
            this.state.dateTo = iso(to);
            this.load();
        }
    }

    onDateChange(which, ev) {
        if (!ev.target.value) {
            return;
        }
        this.state.preset = "custom";
        this.state[which] = ev.target.value;
        this.load();
    }

    // Décale la période de sa propre longueur ; par mois entiers si la période
    // commence un 1er (« Ce mois » → mois précédent, et non 19 jours avant).
    onShift(direction) {
        const from = DateTime.fromISO(this.state.dateFrom);
        const to = DateTime.fromISO(this.state.dateTo);
        let newFrom, newTo;
        if (from.day === 1) {
            const n = (to.year - from.year) * 12 + to.month - from.month + 1;
            newFrom = from.plus({ months: direction * n });
            newTo = newFrom.plus({ months: n - 1 }).endOf("month").startOf("day");
        } else {
            const days = Math.round(to.diff(from, "days").days) + 1;
            newFrom = from.plus({ days: direction * days });
            newTo = to.plus({ days: direction * days });
        }
        const today = DateTime.local().startOf("day");
        if (newFrom > today) {
            return;
        }
        this.state.preset = "custom";
        this.state.dateFrom = iso(newFrom);
        this.state.dateTo = iso(newTo > today ? today : newTo);
        this.load();
    }

    async onPrint() {
        const action = await this.orm.call("hr.employee", "action_print_assiduity", [
            [this.employeeId],
            this.state.dateFrom,
            this.state.dateTo,
        ]);
        await this.actionService.doAction(action);
    }

    // ── Indicateurs ───────────────────────────────────────────────────────
    get d() {
        return this.state.data;
    }

    get employeeSubtitle() {
        return [this.d.employee.job, this.d.employee.department].filter(Boolean).join(" · ");
    }

    get levelLabel() {
        return LEVEL_LABELS[this.d.summary.level] || "";
    }

    get kpis() {
        const s = this.d.summary;
        const p = this.d.previous;
        const kpi = (key, label, value, prevValue, opts = {}) => ({
            key,
            label,
            value,
            sub: opts.sub || "",
            delta: this.delta(value, prevValue, opts.higherIsBetter, opts.unit),
            tone: opts.tone || "",
            drill: opts.drill || null,
        });
        return [
            kpi("presence", "Taux de présence",
                s.presence_rate === null ? "—" : `${s.presence_rate} %`,
                null, { sub: `${s.present_days} / ${s.expected_days} jours attendus`, drill: "all" }),
            kpi("late", "Retards", s.late_count, p.late_count, {
                sub: `${this.minutes(s.late_minutes)} · moy. ${s.late_avg} min · max ${s.late_max} min`,
                tone: s.late_count ? "warning" : "", drill: "late",
            }),
            kpi("early", "Départs anticipés", s.early_count, p.early_count, {
                sub: this.minutes(s.early_minutes), tone: s.early_count ? "warning" : "", drill: "early",
            }),
            kpi("absent", "Absences injustifiées", s.absent_count, p.absent_count, {
                sub: `${s.leave_days} j de congé · ${s.holiday_days} j fériés`,
                tone: s.absent_count ? "danger" : "", drill: "absent",
            }),
            kpi("missing", "Pointages incomplets", s.missing_count, p.missing_count, {
                sub: "entrée sans sortie", tone: s.missing_count ? "warning" : "", drill: "incomplete",
            }),
            kpi("break", "Pauses déjeuner dépassées", s.break_over_count, p.break_over_count, {
                sub: s.break_days
                    ? `${this.minutes(s.break_over_minutes)} en trop · ${s.break_shift_count} décalée(s) · ${s.break_days} j mesurés`
                    : "pause non pointée : non mesurable",
                tone: s.break_over_count ? "warning" : "", drill: "break_over",
            }),
            kpi("balance", "Écart d'heures", this.hours(s.balance_hours, true), null, {
                sub: `${this.hours(s.worked_hours)} faites / ${this.hours(s.planned_hours)} dues`,
                tone: s.balance_hours < 0 ? "danger" : "",
            }),
            kpi("overtime", "Heures sup. approuvées", this.hours(s.overtime_hours), null, {
                sub: s.rest_worked_days ? `${s.rest_worked_days} jour(s) de repos travaillé(s)` : "",
            }),
            kpi("deductions", "Retenues", this.d.deductions.validated, null, {
                sub: `${this.hours(this.d.deductions.retained_hours)} retenues · ${this.d.deductions.to_validate} à décider`,
                tone: this.d.deductions.to_validate ? "warning" : "", drill: "deductions",
            }),
        ];
    }

    delta(value, prev, higherIsBetter = false, unit = "") {
        if (!this.d.previous.comparable || prev === null || prev === undefined || typeof value !== "number") {
            return null;
        }
        const diff = value - prev;
        if (!diff) {
            return { text: "= période préc.", tone: "neutral" };
        }
        const worse = higherIsBetter ? diff < 0 : diff > 0;
        return {
            text: `${diff > 0 ? "▲ +" : "▼ "}${diff}${unit} vs période préc.`,
            tone: worse ? "worse" : "better",
        };
    }

    get scoreDelta() {
        const now = this.d.summary.score;
        const prev = this.d.previous.score;
        if (now === null || prev === null) {
            return null;
        }
        return this.delta(now, prev, true);
    }

    minutes(total) {
        if (!total) {
            return "0 min";
        }
        const h = Math.floor(total / 60);
        const m = total % 60;
        return h ? `${h} h ${String(m).padStart(2, "0")}` : `${m} min`;
    }

    hours(value, signed = false) {
        const v = Number(value || 0);
        const abs = Math.abs(v);
        const h = Math.floor(abs);
        const m = Math.round((abs - h) * 60);
        const sign = v < 0 ? "−" : signed && v > 0 ? "+" : "";
        return `${sign}${h} h ${String(m).padStart(2, "0")}`;
    }

    // ── Comparaison avec le service ───────────────────────────────────────
    get comparisonRows() {
        const c = this.d.comparison;
        const row = (label, key, fmt = (v) => v) => {
            const me = c.employee[key] || 0;
            const avg = c.average[key] || 0;
            const max = Math.max(me, avg, 1);
            return {
                label,
                me: fmt(me),
                avg: fmt(avg),
                meWidth: Math.round((me / max) * 100),
                avgWidth: Math.round((avg / max) * 100),
                worse: key === "worked_hours" ? me < avg : me > avg,
            };
        };
        return [
            row("Retards", "late_count"),
            row("Minutes de retard", "late_minutes"),
            row("Départs anticipés", "early_count"),
            row("Heures travaillées", "worked_hours", (v) => this.hours(v)),
        ];
    }

    // ── Calendrier (un bloc par mois) ─────────────────────────────────────
    get weekdayOrder() {
        const start = (localization.weekStart || 1) - 1; // 0 = lundi
        return [0, 1, 2, 3, 4, 5, 6].map((i) => (start + i) % 7);
    }

    get weekdayShortNames() {
        const names = Info.weekdays("short");
        return this.weekdayOrder.map((wd) => names[wd]);
    }

    get calendarMonths() {
        const months = [];
        let current = null;
        for (const cell of this.d.calendar) {
            const day = DateTime.fromISO(cell.day);
            const key = day.toFormat("yyyy-MM");
            if (!current || current.key !== key) {
                current = {
                    key,
                    label: day.toFormat("LLLL yyyy"),
                    cells: [],
                };
                // Cases vides avant le 1er jour affiché, selon le début de semaine.
                const pad = this.weekdayOrder.indexOf(day.weekday - 1);
                for (let i = 0; i < pad; i++) {
                    current.cells.push({ empty: true, day: `${key}-pad-${i}` });
                }
                months.push(current);
            }
            current.cells.push({
                ...cell,
                number: day.day,
                css: this.cellClass(cell),
                title: this.cellTitle(cell, day),
            });
        }
        return months;
    }

    cellClass(cell) {
        let css = `o_biodoo_cal_${cell.status}`;
        if (cell.status === "present" && (cell.late_min || cell.early_min)) {
            css += " o_biodoo_cal_late";
        }
        if (cell.day === this.d.period.today) {
            css += " o_biodoo_cal_today";
        }
        return css;
    }

    cellTitle(cell, day) {
        const parts = [day.toFormat("cccc dd/LL"), cell.label];
        if (cell.late_min) parts.push(`retard ${cell.late_min} min`);
        if (cell.early_min) parts.push(`départ anticipé ${cell.early_min} min`);
        if (cell.worked) parts.push(`${this.hours(cell.worked)} travaillées`);
        if (cell.note) parts.push(cell.note);
        return parts.join(" · ");
    }

    get calendarCompact() {
        return this.calendarMonths.length > 2;
    }

    // ── Journal ───────────────────────────────────────────────────────────
    get journal() {
        const lines = this.d.journal.map((j) => ({
            ...j,
            dayLabel: DateTime.fromISO(j.day).toFormat("ccc dd/LL"),
            breakLabel: j.break ? `${j.break.start}–${j.break.end}` : "",
            breakOver: j.break ? j.break.over : 0,
            breakShifted: j.break ? j.break.shifted : false,
            issue: Boolean(j.late_min || j.early_min || j.anomalies.length
                || (j.break && (j.break.over || j.break.shifted))
                || ["absent", "missing_punch"].includes(j.status)),
        }));
        return this.state.onlyIssues ? lines.filter((l) => l.issue) : lines;
    }

    onToggleIssues() {
        this.state.onlyIssues = !this.state.onlyIssues;
    }

    // ── Navigation vers les enregistrements ───────────────────────────────
    openRecords(resModel, domain, name) {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: resModel,
            domain,
            views: [[false, "list"], [false, "form"]],
            target: "current",
        });
    }

    openAttendances(ids, name) {
        if (!ids || !ids.length) {
            this.notification.add("Aucun pointage pour ce filtre.", { type: "info" });
            return;
        }
        this.openRecords("hr.attendance", [["id", "in", ids]], name);
    }

    onDrill(key) {
        if (!key) {
            return;
        }
        const drill = this.d.drill;
        const name = `${this.d.employee.name} — `;
        switch (key) {
            case "all":
                return this.openAttendances(drill.attendance_ids, `${name}pointages`);
            case "late":
                return this.openAttendances(drill.late_ids, `${name}retards`);
            case "early":
                return this.openAttendances(drill.early_ids, `${name}départs anticipés`);
            case "incomplete":
                return this.openAttendances(drill.incomplete_ids, `${name}pointages incomplets`);
            case "suspect":
                return this.openAttendances(drill.suspect_ids, `${name}arrivées suspectes`);
            case "corrections":
                return this.openAttendances(drill.correction_ids, `${name}pointages corrigés`);
            case "absent":
                if (!drill.absent_days.length) {
                    this.notification.add("Aucune absence injustifiée sur la période.", { type: "info" });
                    return;
                }
                return this.openRecords("hr.work.entry", [
                    ["employee_id", "=", this.d.employee.id],
                    ["date", "in", drill.absent_days],
                ], `${name}absences`);
            case "deductions":
                if (!drill.deduction_ids.length) {
                    this.notification.add("Aucune retenue sur la période.", { type: "info" });
                    return;
                }
                return this.openRecords("hr.attendance.deduction",
                    [["id", "in", drill.deduction_ids]], `${name}retenues`);
            case "break_over":
                return this.openAttendances(drill.break_over_ids, `${name}pauses dépassées`);
            case "breaks":
                return this.openRecords("zkteco.attendance.break",
                    [["id", "in", drill.break_ids]], `${name}pauses non conformes`);
        }
    }

    onDayClick(entry) {
        if (entry.empty) {
            return;
        }
        this.openAttendances(entry.attendance_ids, `${this.d.employee.name} — ${entry.day}`);
    }

    onOpenEmployee() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "hr.employee",
            res_id: this.d.employee.id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ── Graphiques ────────────────────────────────────────────────────────
    destroyCharts() {
        for (const chart of this.charts) {
            chart.destroy();
        }
        this.charts = [];
    }

    renderCharts() {
        this.destroyCharts();
        if (!this.d || !this.d.summary || !window.Chart) {
            return;
        }
        const el = this.trendRef.el || this.weekdayRef.el;
        const text = el ? getComputedStyle(el).color : COLORS.muted;
        const grid = "rgba(127, 127, 127, 0.15)";
        const Chart = window.Chart;
        const scale = (extra = {}) => ({
            ticks: { color: text },
            grid: { color: grid },
            ...extra,
        });
        const legend = { labels: { color: text, boxWidth: 12 } };

        if (this.trendRef.el) {
            const t = this.d.trend;
            this.charts.push(new Chart(this.trendRef.el, {
                data: {
                    labels: t.map((m) => m.label),
                    datasets: [
                        {
                            type: "line", label: "Score", data: t.map((m) => m.score),
                            borderColor: COLORS.purple, backgroundColor: COLORS.purple,
                            yAxisID: "score", tension: 0.3, spanGaps: true,
                        },
                        {
                            type: "bar", label: "Retards", data: t.map((m) => m.late_count),
                            backgroundColor: COLORS.warning, yAxisID: "count",
                        },
                        {
                            type: "bar", label: "Absences", data: t.map((m) => m.absent_count),
                            backgroundColor: COLORS.danger, yAxisID: "count",
                        },
                    ],
                },
                options: {
                    maintainAspectRatio: false,
                    plugins: { legend },
                    scales: {
                        x: scale(),
                        score: scale({ position: "left", min: 0, max: 100 }),
                        count: scale({ position: "right", beginAtZero: true,
                            suggestedMax: Math.max(1, ...t.map((m) => Math.max(m.late_count, m.absent_count))) * 1.1,
                            grid: { drawOnChartArea: false }, ticks: { color: text, precision: 0 } }),
                    },
                },
            }));
        }

        if (this.weekdayRef.el) {
            const byWd = Object.fromEntries(this.d.weekdays.map((w) => [w.weekday, w]));
            const order = this.weekdayOrder;
            const ds = (label, key, color) => ({
                label, data: order.map((wd) => byWd[wd][key]), backgroundColor: color,
            });
            this.charts.push(new Chart(this.weekdayRef.el, {
                type: "bar",
                data: {
                    labels: this.weekdayShortNames,
                    datasets: [
                        ds("Retards", "late_count", COLORS.warning),
                        ds("Départs anticipés", "early_count", COLORS.teal),
                        ds("Absences", "absent_count", COLORS.danger),
                        ds("Pauses dépassées", "break_over_count", COLORS.purple),
                    ],
                },
                options: {
                    maintainAspectRatio: false,
                    plugins: { legend },
                    scales: {
                        x: scale({ stacked: true }),
                        y: scale({ stacked: true, beginAtZero: true, ticks: { color: text, precision: 0 } }),
                    },
                },
            }));
        }

        if (this.arrivalRef.el) {
            const a = this.d.arrivals;
            const tolerance = this.d.employee.late_tolerance;
            this.charts.push(new Chart(this.arrivalRef.el, {
                type: "line",
                data: {
                    labels: a.map((p) => DateTime.fromISO(p.day).toFormat("dd/LL")),
                    datasets: [
                        {
                            label: "Écart à l'heure prévue (min)",
                            data: a.map((p) => p.offset),
                            borderColor: COLORS.teal,
                            pointBackgroundColor: a.map((p) => (p.late ? COLORS.danger : COLORS.teal)),
                            pointRadius: 4,
                            tension: 0.2,
                        },
                        {
                            label: `Tolérance (${tolerance} min)`,
                            data: a.map(() => tolerance),
                            borderColor: COLORS.warning,
                            borderDash: [6, 4],
                            pointRadius: 0,
                        },
                    ],
                },
                options: {
                    maintainAspectRatio: false,
                    plugins: {
                        legend,
                        tooltip: {
                            callbacks: {
                                label: (ctx) => ctx.datasetIndex === 0
                                    ? `${ctx.parsed.y > 0 ? "+" : ""}${ctx.parsed.y} min`
                                    : ctx.dataset.label,
                            },
                        },
                    },
                    scales: { x: scale(), y: scale({ title: { display: true, text: "min", color: text } }) },
                },
            }));
        }
    }
}

registry.category("actions").add("biodoo_employee_assiduity", EmployeeAssiduity);

import { _t } from "@web/core/l10n/translation";
import {
    deserializeDate,
    deserializeDateTime,
    serializeDate,
    serializeDateTime,
} from "@web/core/l10n/dates";
import { evaluateBooleanExpr } from "@web/core/py_js/py";
import { Domain } from "@web/core/domain";
import { Model } from "@web/model/model";
import {
    COLUMN_UNIT,
    assignLevels,
    columnSpan,
    getColumns,
    getRange,
    getRangeTitle,
    mergeIntervals,
    shiftFocus,
} from "./gantt_helpers";

const { DateTime } = luxon;

/**
 * Données de la vue Gantt : période affichée, lignes (arbre de groupes) et
 * barres placées sur les colonnes.
 *
 * Une ligne :
 *   { id, name, level, fieldName, resId, isGroup, rows (enfants), pills,
 *     levels (nombre de niveaux de barres), aggregates (lignes de groupe),
 *     unavailable (Set des colonnes grisées), progressBar }
 * Une barre :
 *   { id, record, first, last, level, label, colorIndex, decorations }
 */
export class GanttModel extends Model {
    static services = ["orm"];

    setup(params) {
        this.metaData = {
            ...params,
            scale: params.scale || params.defaultScale,
            focusDate: params.focusDate || DateTime.local(),
            closedRows: new Set(params.closedRows || []),
            // Lignes par page (arch : groups_limit) : au-delà, un pager.
            limit: params.limit || params.groupsLimit || 80,
            offset: params.offset || 0,
        };
        this.data = null;
    }

    // ── Chargement ─────────────────────────────────────────────────────────

    async load(searchParams) {
        if (searchParams) {
            this.searchParams = searchParams;
            this.metaData.offset = 0; // nouvelle recherche : première page
        }
        const { context, domain, groupBy } = this.searchParams;
        const metaData = this.metaData;
        // Regrouper par les dates de la barre n'a pas de sens : le temps est
        // déjà en colonnes (ex. « Date : mois » posé par défaut sur l'action).
        const dateFields = [metaData.dateStartField, metaData.dateStopField];
        const searchGroupBy = (groupBy || []).filter((spec) => !dateFields.includes(spec.split(":")[0]));
        metaData.groupBy = searchGroupBy.length ? searchGroupBy : metaData.defaultGroupBy;
        metaData.range = getRange(metaData.scale, metaData.focusDate, metaData.weekStart);
        metaData.columns = getColumns(metaData.scale, metaData.range);
        metaData.title = getRangeTitle(metaData.scale, metaData.range);
        metaData.context = context;
        const groupFields = metaData.groupBy.map((spec) => spec.split(":")[0]);
        const result = await this.orm.call(metaData.resModel, "get_gantt_data", [], {
            domain: Domain.and([domain, this._periodDomain()]).toList(),
            scope_domain: new Domain(domain).toList(),
            groupby: metaData.groupBy,
            read_specification: this._readSpecification(),
            unavailability_fields: metaData.displayUnavailability
                ? groupFields.filter((name) => this._isMany2one(name))
                : [],
            progress_bar_fields: metaData.progressBarFields.filter((name) =>
                groupFields.includes(name)
            ),
            start_date: serializeDateTime(metaData.range.start),
            stop_date: serializeDateTime(metaData.range.stop),
            scale: metaData.scale,
            limit: metaData.groupBy.length ? metaData.limit : undefined,
            offset: metaData.groupBy.length ? metaData.offset : 0,
            context,
        });
        this.data = this._buildData(result);
    }

    /** Page de lignes (pager). */
    async setPage({ offset, limit }) {
        Object.assign(this.metaData, { offset, limit });
        await this.load();
        this.notify();
    }

    hasData() {
        return Boolean(this.data && this.data.records.length);
    }

    // ── Navigation ─────────────────────────────────────────────────────────

    async setScale(scale) {
        this.metaData.scale = scale;
        await this.load();
        this.notify();
    }

    async move(direction) {
        const { metaData } = this;
        metaData.focusDate =
            direction === 0 ? DateTime.local() : shiftFocus(metaData.scale, metaData.focusDate, direction);
        await this.load();
        this.notify();
    }

    async setFocusDate(date) {
        this.metaData.focusDate = date;
        await this.load();
        this.notify();
    }

    toggleRow(rowId) {
        const closed = this.metaData.closedRows;
        if (closed.has(rowId)) {
            closed.delete(rowId);
        } else {
            closed.add(rowId);
        }
        this.notify();
    }

    setAllRows(open) {
        const closed = this.metaData.closedRows;
        closed.clear();
        if (!open) {
            const visit = (rows) => {
                for (const row of rows) {
                    if (row.isGroup) {
                        closed.add(row.id);
                        visit(row.rows);
                    }
                }
            };
            visit(this.data.rows);
        }
        this.notify();
    }

    async reload() {
        await this.load();
        this.notify();
    }

    /** Décale une date de `delta` colonnes (heures, jours ou mois selon l'échelle). */
    _shift(date, delta) {
        const unit = COLUMN_UNIT[this.metaData.scale];
        return date.plus({ [`${unit}s`]: delta });
    }

    _serialize(fieldName, date) {
        return this._isDate(fieldName) ? serializeDate(date) : serializeDateTime(date);
    }

    /** Une barre peut-elle être déplacée ou étirée à cette échelle ? */
    canReschedule() {
        const { dateStartField, dateStopField, scale } = this.metaData;
        // Un champ date ne se décale pas d'une heure.
        return !(scale === "day" && (this._isDate(dateStartField) || this._isDate(dateStopField)));
    }

    /**
     * Déplace une barre de `delta` colonnes et, si elle change de ligne, lui
     * donne les valeurs de regroupement de la ligne d'arrivée.
     */
    async reschedule(pill, delta, fromRow, toRow) {
        const { dateStartField, dateStopField, resModel, context } = this.metaData;
        const record = pill.record;
        const vals = {};
        if (delta) {
            const read = (name) =>
                this._isDate(name) ? deserializeDate(record[name]) : deserializeDateTime(record[name]);
            vals[dateStartField] = this._serialize(dateStartField, this._shift(read(dateStartField), delta));
            if (dateStopField !== dateStartField && record[dateStopField]) {
                vals[dateStopField] = this._serialize(dateStopField, this._shift(read(dateStopField), delta));
            }
        }
        if (toRow && fromRow && toRow.id !== fromRow.id) {
            for (const [name, value] of Object.entries(toRow.values || {})) {
                if (fromRow.values?.[name] !== value) {
                    vals[name] = value;
                }
            }
        }
        if (Object.keys(vals).length) {
            await this.orm.write(resModel, [record.id], vals, { context });
        }
        await this.reload();
    }

    /** Étire (ou raccourcit) la fin d'une barre de `delta` colonnes. */
    async resize(pill, delta) {
        const { dateStopField, resModel, context } = this.metaData;
        const record = pill.record;
        if (!delta || !record[dateStopField]) {
            return;
        }
        const stop = this._isDate(dateStopField)
            ? deserializeDate(record[dateStopField])
            : deserializeDateTime(record[dateStopField]);
        await this.orm.write(
            resModel,
            [record.id],
            { [dateStopField]: this._serialize(dateStopField, this._shift(stop, delta)) },
            { context }
        );
        await this.reload();
    }

    /** Contexte de création pour les colonnes [first, last[ d'une ligne. */
    createContext(row, first, last) {
        const { columns, dateStartField, dateStopField, context } = this.metaData;
        const start = columns[first].start;
        const stop = columns[last - 1].stop;
        const defaults = {
            [`default_${dateStartField}`]: this._serialize(dateStartField, start),
        };
        if (dateStopField !== dateStartField) {
            defaults[`default_${dateStopField}`] = this._isDate(dateStopField)
                ? serializeDate(stop.minus({ days: 1 }))
                : serializeDateTime(stop);
        }
        for (const [name, value] of Object.entries(row.values || {})) {
            if (value !== false && value !== null && value !== undefined) {
                defaults[`default_${name}`] = value;
            }
        }
        return { ...context, ...defaults };
    }

    async unlinkRecords(ids) {
        await this.orm.unlink(this.metaData.resModel, ids, { context: this.metaData.context });
        await this.reload();
    }

    // ── Construction de la requête ─────────────────────────────────────────

    _isMany2one(name) {
        return this.metaData.fields[name]?.type === "many2one";
    }

    _isDate(name) {
        return this.metaData.fields[name]?.type === "date";
    }

    _periodDomain() {
        const { dateStartField, dateStopField, range } = this.metaData;
        const lastDay = range.stop.minus({ days: 1 });
        const startCond = this._isDate(dateStartField)
            ? [dateStartField, "<=", serializeDate(lastDay)]
            : [dateStartField, "<", serializeDateTime(range.stop)];
        const stopCond = this._isDate(dateStopField)
            ? [dateStopField, ">=", serializeDate(range.start)]
            : [dateStopField, ">", serializeDateTime(range.start)];
        if (dateStopField !== dateStartField && !this.metaData.fields[dateStopField].required) {
            // Fin vide (ex. pointage sans sortie) : la barre va jusqu'à la fin
            // de son jour, donc seules celles qui commencent la veille ou
            // après peuvent apparaître.
            const dayBefore = range.start.minus({ days: 1 });
            const startFrom = this._isDate(dateStartField)
                ? [dateStartField, ">=", serializeDate(dayBefore)]
                : [dateStartField, ">=", serializeDateTime(dayBefore)];
            return [startCond, "|", "&", [dateStopField, "=", false], startFrom, stopCond];
        }
        return [startCond, stopCond];
    }

    _readSpecification() {
        const { fields, readFields } = this.metaData;
        const spec = {};
        for (const name of readFields) {
            spec[name] = ["many2one", "many2many", "one2many"].includes(fields[name].type)
                ? { fields: { display_name: {} } }
                : {};
        }
        return spec;
    }

    // ── Construction des lignes et des barres ──────────────────────────────

    _recordBounds(record) {
        const { dateStartField, dateStopField } = this.metaData;
        const parse = (name, value, isStop) => {
            if (!value) {
                return null;
            }
            if (this._isDate(name)) {
                const day = deserializeDate(value);
                return isStop ? day.plus({ days: 1 }) : day;
            }
            return deserializeDateTime(value);
        };
        const start = parse(dateStartField, record[dateStartField], false);
        let stop = parse(dateStopField, record[dateStopField], true);
        if (start && !stop) {
            // Sans fin (ex. pointage sans sortie) : jusqu'à maintenant si c'est
            // aujourd'hui, sinon jusqu'à la fin de ce jour-là.
            const now = DateTime.local();
            stop = start.hasSame(now, "day") && now > start ? now : start.endOf("day");
        }
        if (start && stop <= start) {
            stop = start.plus({ minutes: 1 });
        }
        return { start, stop };
    }

    _colorIndex(record) {
        const { colorField, fields } = this.metaData;
        if (!colorField) {
            return 0;
        }
        const value = record[colorField];
        if (fields[colorField].type === "many2one") {
            return value ? (value.id % 11) + 1 : 0;
        }
        return Number(value) || 0;
    }

    _decorations(record) {
        const classes = [];
        for (const [style, expr] of Object.entries(this.metaData.decorations)) {
            try {
                if (evaluateBooleanExpr(expr, this._evalContext(record))) {
                    classes.push(`decoration-${style}`);
                }
            } catch {
                // expression invalide pour cet enregistrement : sans décoration
            }
        }
        return classes;
    }

    _evalContext(record) {
        const values = {};
        for (const [name, value] of Object.entries(record)) {
            values[name] = value && typeof value === "object" && "id" in value ? value.id : value;
        }
        return { ...this.metaData.context, ...values, context_today: () => DateTime.local() };
    }

    _makePill(record) {
        const { start, stop } = this._recordBounds(record);
        if (!start) {
            return null;
        }
        const span = columnSpan(this.metaData.columns, start, stop);
        if (!span) {
            return null;
        }
        return {
            id: record.id,
            record,
            start,
            stop,
            first: span.first,
            last: span.last,
            level: 0,
            label: record.display_name || "",
            colorIndex: this._colorIndex(record),
            decorations: this._decorations(record),
        };
    }

    _groupValueLabel(fieldName, value) {
        const field = this.metaData.fields[fieldName];
        if (value === false || value === null || value === undefined) {
            return _t("Aucun(e) %s", field?.string || fieldName);
        }
        if (Array.isArray(value)) {
            return value[1];
        }
        if (field?.type === "selection") {
            return (field.selection.find(([key]) => key === value) || [value, value])[1];
        }
        if (field?.type === "boolean") {
            return value ? _t("Oui") : _t("Non");
        }
        return String(value);
    }

    _buildData(result) {
        const { metaData } = this;
        const recordsById = Object.fromEntries(result.records.map((r) => [r.id, r]));
        const pillsById = {};
        for (const record of result.records) {
            const pill = this._makePill(record);
            if (pill) {
                pillsById[record.id] = pill;
            }
        }
        const groupBy = metaData.groupBy;
        const unavailabilities = this._parseUnavailabilities(result.unavailabilities || {});
        const progressBars = result.progress_bars || {};

        const root = { id: "root", rows: [], byKey: {}, values: {} };
        for (const group of result.groups) {
            let parent = root;
            const path = [];
            groupBy.forEach((spec, depth) => {
                const fieldName = spec.split(":")[0];
                const value = group[spec];
                const key = Array.isArray(value) ? value[0] : value;
                path.push(JSON.stringify(key ?? false));
                const id = `${depth}:${path.join("/")}`;
                let row = parent.byKey[id];
                if (!row) {
                    const resId = Array.isArray(value) ? value[0] : false;
                    const values = { ...parent.values };
                    if (!spec.includes(":")) {
                        values[fieldName] = Array.isArray(value) ? value[0] : value;
                    }
                    row = {
                        values,
                        id,
                        name: this._groupValueLabel(fieldName, value),
                        level: depth,
                        fieldName,
                        resId,
                        isGroup: depth < groupBy.length - 1,
                        rows: [],
                        byKey: {},
                        recordIds: [],
                        unavailable: this._unavailableColumns(unavailabilities[fieldName]?.[resId]),
                        progressBar: resId ? progressBars[fieldName]?.[resId] || null : null,
                    };
                    parent.byKey[id] = row;
                    parent.rows.push(row);
                }
                row.recordIds.push(...group.__record_ids);
                parent = row;
            });
            if (!groupBy.length) {
                root.recordIds = group.__record_ids;
            }
        }
        if (!groupBy.length) {
            root.rows.push({
                id: "all",
                values: {},
                name: "",
                level: 0,
                isGroup: false,
                rows: [],
                recordIds: root.recordIds || [],
                unavailable: new Set(),
                progressBar: null,
            });
        }
        const finish = (row, parent) => {
            row.recordIds = [...new Set(row.recordIds)];
            // Sous-ligne sans indisponibilité propre (ex. type sous un
            // employé) : elle reprend celle de la ligne parente.
            if (parent && !row.unavailable.size && parent.unavailable.size) {
                row.unavailable = parent.unavailable;
            }
            const rowPills = row.recordIds.map((id) => pillsById[id]).filter(Boolean);
            // Total de la ligne sur la période affichée (heures, ou nombre).
            row.total = this._columnValues(rowPills).reduce((a, b) => a + b, 0);
            if (row.isGroup) {
                row.rows.forEach((child) => finish(child, row));
                row.aggregates = this._aggregate(rowPills, true);
            } else {
                row.pills = rowPills.map((p) => ({ ...p }));
                row.levels = Math.max(assignLevels(row.pills), 1);
            }
            delete row.byKey;
        };
        root.rows.forEach((row) => finish(row, null));
        return {
            rows: root.rows,
            records: result.records,
            recordsById,
            length: result.length,
            total: metaData.totalRow ? this._aggregate(Object.values(pillsById)) : null,
            legend: this._legend(Object.values(pillsById)),
        };
    }

    /**
     * Légende de ce qui est affiché (période, filtres et recherche compris) :
     * une entrée par valeur du champ de légende, avec sa couleur, le nombre de
     * barres et la somme du champ de consolidation. Vide sans champ de légende.
     */
    _legend(pills) {
        const { legendField, consolidationField, fields } = this.metaData;
        if (!legendField || !fields[legendField]) {
            return [];
        }
        const byKey = new Map();
        for (const pill of pills) {
            const value = pill.record[legendField];
            const key = value && typeof value === "object" ? value.id : value;
            let label;
            if (value && typeof value === "object") {
                label = value.display_name;
            } else if (fields[legendField].type === "selection") {
                label = (fields[legendField].selection.find(([k]) => k === value) || [value, value])[1];
            } else {
                label = value ? String(value) : _t("Sans %s", fields[legendField].string);
            }
            if (!byKey.has(key)) {
                byKey.set(key, { key, label, colorIndex: pill.colorIndex, count: 0, total: 0 });
            }
            const entry = byKey.get(key);
            entry.count += 1;
            entry.total += consolidationField ? Number(pill.record[consolidationField]) || 0 : 1;
        }
        return [...byKey.values()].sort((a, b) => b.total - a.total || a.label.localeCompare(b.label));
    }

    /** Clé de légende d'une barre (pour mettre un type en avant). */
    legendKey(pill) {
        const value = pill.record[this.metaData.legendField];
        return value && typeof value === "object" ? value.id : value;
    }

    _parseUnavailabilities(raw) {
        const parsed = {};
        for (const [fieldName, byResId] of Object.entries(raw)) {
            parsed[fieldName] = {};
            for (const [resId, intervals] of Object.entries(byResId)) {
                parsed[fieldName][resId] = mergeIntervals(
                    intervals.map((i) => ({
                        start: deserializeDateTime(i.start),
                        stop: deserializeDateTime(i.stop),
                    }))
                );
            }
        }
        return parsed;
    }

    /** Colonnes entièrement couvertes par une indisponibilité. */
    _unavailableColumns(intervals) {
        const unavailable = new Set();
        if (!intervals || !intervals.length) {
            return unavailable;
        }
        for (const column of this.metaData.columns) {
            if (intervals.some((i) => i.start <= column.start && i.stop >= column.stop)) {
                unavailable.add(column.index);
            }
        }
        return unavailable;
    }

    /**
     * Valeur par colonne : le nombre de barres, ou la somme du champ de
     * consolidation répartie sur TOUTE la durée de la barre — 4 h de 8 h à
     * 12 h = 1 h par heure ; une barre coupée au bord de la période ne compte
     * que sa part visible.
     */
    _columnValues(pills) {
        const { columns, consolidationField, scale } = this.metaData;
        const values = new Array(columns.length).fill(0);
        const unit = `${COLUMN_UNIT[scale]}s`;
        for (const pill of pills) {
            if (!pill) {
                continue;
            }
            let amount = 1;
            if (consolidationField) {
                const units = Math.max(1, Math.ceil(pill.stop.diff(pill.start, unit)[unit] - 1e-9));
                const fullWidth = Math.max(units, pill.last - pill.first);
                amount = (Number(pill.record[consolidationField]) || 0) / fullWidth;
            }
            for (let i = pill.first; i < pill.last; i++) {
                values[i] += amount;
            }
        }
        return values;
    }

    /**
     * Ligne de groupe ou de total : par colonne, le nombre de barres (ou la
     * somme du champ de consolidation). Une case non vide = un segment ; avec
     * `merge`, les cases voisines de même valeur n'en font qu'un (lignes de
     * groupe : un seul trait pour une semaine de 8 h).
     */
    _aggregate(pills, merge = false) {
        const values = this._columnValues(pills);
        const segments = [];
        values.forEach((value, index) => {
            value = Math.round(value * 1000) / 1000;
            const previous = segments.at(-1);
            if (merge && previous && previous.value === value && previous.last === index) {
                previous.last = index + 1;
            } else if (value) {
                segments.push({ first: index, last: index + 1, value });
            }
        });
        return segments;
    }
}

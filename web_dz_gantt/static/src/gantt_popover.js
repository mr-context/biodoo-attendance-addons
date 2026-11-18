import { Component } from "@odoo/owl";
import { deserializeDate, deserializeDateTime, formatDate } from "@web/core/l10n/dates";

const { DateTime } = luxon;

/** Date et heure courtes, dans la langue de l'utilisateur, sans secondes. */
function formatShortDateTime(value) {
    return value.toLocaleString(DateTime.DATETIME_SHORT);
}
import { registry } from "@web/core/registry";

/**
 * Bulle d'une barre : libellé, période, champs de la vue, et les actions
 * permises (ouvrir, supprimer).
 */
export class GanttPopover extends Component {
    static template = "web_dz_gantt.GanttPopover";
    static props = {
        close: Function,
        pill: Object,
        fields: Object,
        popoverFields: Array,
        dateStartField: String,
        dateStopField: String,
        canEdit: Boolean,
        canDelete: Boolean,
        onOpen: Function,
        onDelete: Function,
        extraLines: { type: Array, optional: true },
        title: { type: String, optional: true },
        showPeriod: { type: Boolean, optional: true },
    };

    get period() {
        const { pill, fields, dateStartField, dateStopField } = this.props;
        const record = pill.record;
        const format = (name) => {
            const value = name === dateStopField && fields[name].type === "date"
                ? pill.stop.minus({ days: 1 })
                : name === dateStartField ? pill.start : pill.stop;
            return fields[name].type === "date" ? formatDate(value) : formatShortDateTime(value);
        };
        const start = format(dateStartField);
        const stop = format(dateStopField);
        return start === stop || !record[dateStopField] ? start : `${start} → ${stop}`;
    }

    get lines() {
        const { pill, fields, popoverFields } = this.props;
        const lines = [];
        for (const { name, string, widget } of popoverFields) {
            const field = fields[name];
            const value = pill.record[name];
            if (value === false || value === null || value === undefined || value === "") {
                continue;
            }
            const formatters = registry.category("formatters");
            const type = widget && formatters.contains(widget) ? widget : field.type;
            let text;
            if (field.type === "date") {
                text = formatDate(deserializeDate(value));
            } else if (field.type === "datetime") {
                text = formatShortDateTime(deserializeDateTime(value));
            } else if (["many2one"].includes(field.type)) {
                text = value.display_name;
            } else if (["many2many", "one2many"].includes(field.type)) {
                text = value.map((v) => v.display_name).join(", ");
            } else if (formatters.contains(type)) {
                text = formatters.get(type)(value, { field, selection: field.selection });
            } else {
                text = String(value);
            }
            lines.push({ label: string, text });
        }
        return [...lines, ...(this.props.extraLines || [])];
    }

    onOpen() {
        this.props.close();
        this.props.onOpen(this.props.pill);
    }

    onDelete() {
        this.props.close();
        this.props.onDelete(this.props.pill);
    }
}

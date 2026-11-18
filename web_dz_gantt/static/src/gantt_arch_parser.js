import { exprToBoolean } from "@web/core/utils/strings";
import { visitXML } from "@web/core/utils/xml";

export const SCALES = ["day", "week", "month", "year"];

/** Noms ressemblant à des champs dans une expression Python (décorations). */
function identifiers(expr) {
    return [...expr.matchAll(/\b([a-z_][a-z0-9_]*)\b/g)].map((m) => m[1]);
}

function fieldList(value) {
    return (value || "")
        .split(",")
        .map((name) => name.trim())
        .filter(Boolean);
}

/**
 * Lit la description <gantt>. Attributs reconnus (les mêmes que ceux
 * documentés pour la vue Gantt d'Odoo) :
 *
 *  date_start, date_stop        champs de début et de fin (obligatoires)
 *  color                        champ de couleur (entier ou many2one)
 *  default_group_by             regroupement des lignes (plusieurs : « a,b »)
 *  scales                       échelles proposées (day,week,month,year)
 *  default_scale | default_range échelle à l'ouverture (month par défaut)
 *  total_row                    ligne de total en bas
 *  display_unavailability       grise les périodes indisponibles des lignes
 *  progress_bar                 champs de regroupement avec barre de progression
 *  consolidation                champ additionné dans les lignes de groupe
 *  create, edit, delete         droits d'action dans la vue
 *  disable_drag_drop            barres ni déplaçables ni étirables
 *  groups_limit                 lignes par page (80 par défaut), puis un pager
 *  week_start                   premier jour de la semaine (1 = lundi … 7 =
 *                               dimanche) ; par défaut, celui de la langue
 *  legend_field                 champ décrit par la légende des couleurs
 *                               (par défaut : `color` s'il est un many2one)
 *  form_view_id                 formulaire ouvert depuis la bulle
 *  decoration-<style>           style des barres selon une expression
 *
 * Les <field> enfants sont lus avec les barres et montrés dans la bulle.
 */
export class GanttArchParser {
    parse(xmlDoc, fields) {
        const info = {
            dateStartField: null,
            dateStopField: null,
            colorField: null,
            defaultGroupBy: [],
            scales: [...SCALES],
            defaultScale: "month",
            totalRow: false,
            displayUnavailability: false,
            progressBarFields: [],
            consolidationField: null,
            canCreate: true,
            canEdit: true,
            canDelete: true,
            disableDragDrop: false,
            legendField: null,
            groupsLimit: 80,
            weekStart: null,
            formViewId: false,
            decorations: {},
            popoverFields: [],
            string: "",
        };
        const used = new Set(["display_name"]);
        visitXML(xmlDoc, (node) => {
            if (node.tagName === "gantt") {
                const attr = (name) => node.getAttribute(name);
                info.string = attr("string") || "";
                info.dateStartField = attr("date_start");
                info.dateStopField = attr("date_stop");
                info.colorField = attr("color") || null;
                info.defaultGroupBy = fieldList(attr("default_group_by"));
                const scales = fieldList(attr("scales")).filter((s) => SCALES.includes(s));
                if (scales.length) {
                    info.scales = scales;
                }
                const scale = attr("default_scale") || attr("default_range");
                info.defaultScale = info.scales.includes(scale) ? scale : info.scales.includes("month") ? "month" : info.scales[0];
                info.totalRow = exprToBoolean(attr("total_row") || "");
                info.displayUnavailability = exprToBoolean(attr("display_unavailability") || "");
                info.progressBarFields = fieldList(attr("progress_bar"));
                info.consolidationField = attr("consolidation") || null;
                info.canCreate = exprToBoolean(attr("create") || "", true);
                info.canEdit = exprToBoolean(attr("edit") || "", true);
                info.canDelete = exprToBoolean(attr("delete") || "", true);
                info.disableDragDrop = exprToBoolean(attr("disable_drag_drop") || "");
                info.legendField = attr("legend_field") || null;
                info.groupsLimit = Number(attr("groups_limit")) || 80;
                const weekStart = Number(attr("week_start"));
                info.weekStart = weekStart >= 1 && weekStart <= 7 ? weekStart : null;
                if (!info.legendField && info.colorField && fields[info.colorField]?.type === "many2one") {
                    info.legendField = info.colorField;
                }
                info.formViewId = attr("form_view_id") ? Number(attr("form_view_id")) : false;
                for (const { name, value } of node.attributes) {
                    if (name.startsWith("decoration-")) {
                        info.decorations[name.slice("decoration-".length)] = value;
                        identifiers(value).forEach((id) => fields[id] && used.add(id));
                    }
                }
                for (const name of [info.dateStartField, info.dateStopField, info.colorField,
                                    info.consolidationField, info.legendField]) {
                    if (name) {
                        used.add(name);
                    }
                }
            } else if (node.tagName === "field") {
                const name = node.getAttribute("name");
                if (fields[name]) {
                    used.add(name);
                    if (!exprToBoolean(node.getAttribute("invisible") || "")) {
                        info.popoverFields.push({
                            name,
                            string: node.getAttribute("string") || fields[name].string,
                            widget: node.getAttribute("widget") || null,
                        });
                    }
                }
            } else if (node.tagName === "templates") {
                return false;
            }
        });
        info.readFields = [...used].filter((name) => fields[name]);
        return info;
    }
}

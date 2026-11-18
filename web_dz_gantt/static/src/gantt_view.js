import { registry } from "@web/core/registry";
import { GanttArchParser } from "./gantt_arch_parser";
import { GanttController } from "./gantt_controller";
import { GanttModel } from "./gantt_model";
import { GanttRenderer } from "./gantt_renderer";

/**
 * Vue Gantt.
 *
 * ── Nouvelle vue Gantt en 3 fichiers (exemple : les congés) ─────────────
 *
 * 1. La vue (XML), dans le module métier :
 *
 *        <gantt js_class="hr_gantt" date_start="date_from" date_stop="date_to"
 *               default_group_by="employee_id" color="holiday_status_id"
 *               display_unavailability="1" scales="week,month" default_scale="month">
 *            <field name="holiday_status_id"/>   <!-- lu et montré dans la bulle -->
 *        </gantt>
 *
 *    + un `ir.actions.act_window.view` (view_mode « gantt ») sur l'action.
 *
 * 2. Le modèle (Python) : pour les lignes d'employés, hériter du mixin RH
 *    (heures hors planning grisées, employés sans donnée affichés) :
 *
 *        class HrLeave(models.Model):
 *            _name = 'hr.leave'
 *            _inherit = ['hr.leave', 'hr.dz.gantt.employee.mixin']
 *
 * 3. Facultatif, l'affichage (JS) : dériver le Renderer pour le libellé des
 *    barres, la bulle, une légende fixe ou une barre d'actions :
 *
 *        class LeaveGanttRenderer extends HrGanttRenderer {
 *            getPillLabel(pill) { return pill.record.holiday_status_id.display_name; }
 *        }
 *        registry.category("views").add("leave_gantt", { ...hrGanttView, Renderer: LeaveGanttRenderer });
 *
 *    et mettre js_class="leave_gantt" dans la vue.
 *
 * ── Pièges connus ─────────────────────────────────────────────────────────
 *
 *  - Type de vue `gantt` : ce module exclut `web_gantt` (Enterprise), les
 *    deux ne peuvent pas être installés ensemble.
 *  - Couleur portée par un many2one : l'index vient de l'id (id % 11), donc
 *    de la base ; pour une couleur stable, utiliser un champ entier
 *    (ex. `color` related du type, comme les prestations).
 *  - `date_start == date_stop` sur un champ date : une barre d'un jour ; le
 *    déplacement et l'étirement n'ont alors pas de sens (disable_drag_drop).
 *  - Fin vide (date_stop facultatif) : la barre va jusqu'à maintenant ou la
 *    fin de son jour ; le domaine de période l'inclut.
 *  - hr_dz_gantt dépend du moteur de prestations (hr_dz_work_entry) : les
 *    heures grisées viennent de son `_planned_intervals`.
 *  - Lignes d'employés sans donnée : ajoutées seulement si la recherche se
 *    traduit sûrement en domaine employé (pas de OU) ; voir
 *    `hr.dz.gantt.employee.mixin._dz_employee_scope`.
 *  - OWL : ne jamais modifier `this` dans un getter appelé par le gabarit
 *    (le contexte de rendu est une copie du composant).
 *
 * Attributs de <gantt> : voir gantt_arch_parser.js. Côté serveur :
 * `get_gantt_data`, `_gantt_unavailability`, `_gantt_extra_groups`,
 * `_gantt_progress_bar` (web_dz_gantt/models/models.py).
 */
export const ganttView = {
    type: "gantt",
    Controller: GanttController,
    Renderer: GanttRenderer,
    Model: GanttModel,
    ArchParser: GanttArchParser,
    searchMenuTypes: ["filter", "groupBy", "favorite"],
    buttonTemplate: "web_dz_gantt.GanttView.Buttons",

    props: (genericProps, view) => {
        const { arch, fields, resModel, state } = genericProps;
        const archInfo = new view.ArchParser().parse(arch, fields);
        const modelParams = {
            ...archInfo,
            ...(state?.metaData || {}),
            fields,
            resModel,
        };
        return {
            ...genericProps,
            Model: view.Model,
            modelParams,
            Renderer: view.Renderer,
            buttonTemplate: view.buttonTemplate,
        };
    },
};

registry.category("views").add("gantt", ganttView);

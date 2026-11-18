import { GanttRenderer } from "@web_dz_gantt/gantt_renderer";

/** En-tête de ligne avec l'avatar de l'employé (ou de l'utilisateur). */
export class HrGanttRenderer extends GanttRenderer {
    static rowHeaderTemplate = "hr_dz_gantt.RowHeader";

    avatarUrl(row) {
        const model = { employee_id: "hr.employee.public", user_id: "res.users" }[row.fieldName];
        return model && row.resId ? `/web/image/${model}/${row.resId}/avatar_128` : null;
    }
}

import { registry } from "@web/core/registry";
import { ganttView } from "@web_dz_gantt/gantt_view";
import { HrGanttRenderer } from "./hr_gantt_renderer";

export const hrGanttView = {
    ...ganttView,
    Renderer: HrGanttRenderer,
};

registry.category("views").add("hr_gantt", hrGanttView);

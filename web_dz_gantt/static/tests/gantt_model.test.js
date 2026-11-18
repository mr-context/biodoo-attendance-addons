import { describe, expect, test } from "@odoo/hoot";
import { makeMockEnv } from "@web/../tests/web_test_helpers";
import { GanttModel } from "@web_dz_gantt/gantt_model";
import { getColumns, getRange } from "@web_dz_gantt/gantt_helpers";

const { DateTime } = luxon;

describe.current.tags("headless");

/** Modèle prêt à construire des lignes, sans serveur. */
function makeModel(extra = {}) {
    const fields = {
        date: { type: "date", string: "Date" },
        duration: { type: "float", string: "Durée" },
        employee_id: { type: "many2one", string: "Employé" },
        work_entry_type_id: { type: "many2one", string: "Type" },
        check_in: { type: "datetime", string: "Arrivée", required: true },
        check_out: { type: "datetime", string: "Sortie", required: false },
    };
    const model = new GanttModel({}, {
        fields, resModel: "x", readFields: Object.keys(fields),
        dateStartField: "date", dateStopField: "date", colorField: null,
        consolidationField: "duration", legendField: "work_entry_type_id",
        defaultScale: "month", defaultGroupBy: ["employee_id"], decorations: {},
        progressBarFields: [], totalRow: true, ...extra,
    }, { orm: {} });
    const metaData = model.metaData;
    metaData.focusDate = DateTime.local(2026, 10, 1);
    metaData.range = getRange("month", metaData.focusDate);
    metaData.columns = getColumns("month", metaData.range);
    metaData.groupBy = ["employee_id"];
    metaData.context = {};
    return model;
}

const record = (id, day, duration, type, employee = 1) => ({
    id, display_name: `#${id}`, date: day, duration,
    employee_id: { id: employee, display_name: `E${employee}` },
    work_entry_type_id: { id: type, display_name: `T${type}` },
});

describe("lignes, totaux et légende", () => {
    test("une ligne par employé, avec son total sur la période", async () => {
        await makeMockEnv();
        const model = makeModel();
        const data = model._buildData({
            groups: [
                { employee_id: [1, "E1"], __record_ids: [1, 2] },
                { employee_id: [2, "E2"], __record_ids: [] },   // absent : ligne vide
            ],
            records: [record(1, "2026-10-04", 8, 10), record(2, "2026-10-05", 6, 11)],
            length: 2,
        });
        expect(data.rows.map((r) => r.name)).toEqual(["E1", "E2"]);
        expect(data.rows[0].total).toBe(14);
        expect(data.rows[1].total).toBe(0);
        expect(data.total.map((s) => s.value)).toEqual([8, 6]);
    });

    test("la légende suit ce qui est affiché, du plus grand total au plus petit", async () => {
        await makeMockEnv();
        const model = makeModel();
        const data = model._buildData({
            groups: [{ employee_id: [1, "E1"], __record_ids: [1, 2, 3] }],
            records: [record(1, "2026-10-04", 8, 10), record(2, "2026-10-05", 8, 10),
                      record(3, "2026-10-06", 2, 11)],
            length: 1,
        });
        expect(data.legend.map((e) => [e.label, e.count, e.total])).toEqual([["T10", 2, 16], ["T11", 1, 2]]);
    });

    test("une barre coupée au bord de la période ne compte que sa part visible", async () => {
        await makeMockEnv();
        const model = makeModel({ dateStartField: "check_in", dateStopField: "check_out",
                                  consolidationField: "duration" });
        // 48 h du 30/09 0 h au 2/10 0 h : un seul jour visible en octobre → 24 h.
        const values = model._columnValues([{
            start: DateTime.local(2026, 9, 30), stop: DateTime.local(2026, 10, 2),
            first: 0, last: 1, record: { duration: 48 },
        }]);
        expect(values[0]).toBe(24);
    });
});

describe("période demandée au serveur", () => {
    test("une fin facultative vide (pointage sans sortie) n'est pas écartée", async () => {
        await makeMockEnv();
        const model = makeModel({ dateStartField: "check_in", dateStopField: "check_out" });
        const domain = JSON.stringify(model._periodDomain());
        expect(domain).toInclude('["check_out","=",false]');
    });

    test("une fin obligatoire : simple chevauchement", async () => {
        await makeMockEnv();
        const model = makeModel();
        expect(model._periodDomain()).toHaveLength(2);
    });
});

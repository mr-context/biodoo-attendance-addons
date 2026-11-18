import { describe, expect, test } from "@odoo/hoot";
import { makeMockEnv } from "@web/../tests/web_test_helpers";
import {
    assignLevels,
    columnSpan,
    getColumns,
    getRange,
    mergeIntervals,
    shiftFocus,
} from "@web_dz_gantt/gantt_helpers";

const { DateTime } = luxon;

describe.current.tags("headless");

describe("périodes et colonnes", () => {
    test("le mois a une colonne par jour", () => {
        const range = getRange("month", DateTime.local(2026, 10, 17, 15));
        expect(range.start.toISODate()).toBe("2026-10-01");
        expect(range.stop.toISODate()).toBe("2026-11-01");
        expect(getColumns("month", range)).toHaveLength(31);
    });

    test("le jour a 24 colonnes d'une heure", () => {
        const range = getRange("day", DateTime.local(2026, 10, 14, 9));
        const columns = getColumns("day", range);
        expect(columns).toHaveLength(24);
        expect(columns[8].label).toBe("08");
    });

    test("l'année a 12 colonnes, la semaine 7", async () => {
        await makeMockEnv(); // la semaine dépend de la langue (premier jour)
        const focus = DateTime.local(2026, 10, 14);
        expect(getColumns("year", getRange("year", focus))).toHaveLength(12);
        expect(getColumns("week", getRange("week", focus))).toHaveLength(7);
    });

    test("la semaine de travail algérienne commence le dimanche (week_start=7)", () => {
        const range = getRange("week", DateTime.local(2026, 10, 14), 7); // mercredi
        expect(range.start.toISODate()).toBe("2026-10-11");                // dimanche
        expect(range.stop.toISODate()).toBe("2026-10-18");
    });

    test("suivant / précédent avancent d'une période", async () => {
        await makeMockEnv();
        const focus = DateTime.local(2026, 1, 31);
        expect(shiftFocus("month", focus, 1).month).toBe(2);
        expect(shiftFocus("week", focus, -1).toISODate()).toBe("2026-01-24");
    });
});

describe("placement des barres", () => {
    const columns = getColumns("month", getRange("month", DateTime.local(2026, 10, 1)));

    test("une journée occupe une colonne", () => {
        const start = DateTime.local(2026, 10, 5);
        expect(columnSpan(columns, start, start.plus({ days: 1 }))).toEqual({ first: 4, last: 5 });
    });

    test("une barre qui déborde est coupée aux bords de la période", () => {
        const span = columnSpan(columns, DateTime.local(2026, 9, 28), DateTime.local(2026, 10, 3));
        expect(span).toEqual({ first: 0, last: 2 });
    });

    test("hors période : rien", () => {
        expect(columnSpan(columns, DateTime.local(2026, 11, 2), DateTime.local(2026, 11, 3))).toBe(null);
    });

    test("des barres qui se chevauchent passent sur des niveaux différents", () => {
        const pills = [
            { id: 1, first: 0, last: 3 },
            { id: 2, first: 1, last: 2 },
            { id: 3, first: 3, last: 4 },
        ];
        expect(assignLevels(pills)).toBe(2);
        const level = Object.fromEntries(pills.map((p) => [p.id, p.level]));
        expect(level).toEqual({ 1: 0, 2: 1, 3: 0 });
    });

    test("les indisponibilités voisines sont fusionnées", () => {
        const at = (h) => DateTime.local(2026, 10, 1, h);
        const merged = mergeIntervals([
            { start: at(12), stop: at(13) },
            { start: at(0), stop: at(8) },
            { start: at(13), stop: at(23) },
        ]);
        expect(merged).toHaveLength(2);
        expect(merged[1].stop.hour).toBe(23);
    });
});

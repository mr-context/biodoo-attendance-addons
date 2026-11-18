import { getStartOfLocalWeek } from "@web/core/l10n/dates";

const { DateTime } = luxon;

/** Unité d'une colonne selon l'échelle. */
export const COLUMN_UNIT = { day: "hour", week: "day", month: "day", year: "month" };

/**
 * Période affichée : [start, stop[ en heure locale. `weekStart` (1 = lundi …
 * 7 = dimanche) remplace le premier jour de la langue de l'utilisateur.
 */
export function getRange(scale, focus, weekStart = null) {
    let start;
    if (scale === "day") {
        start = focus.startOf("day");
        return { start, stop: start.plus({ days: 1 }) };
    }
    if (scale === "week") {
        start = weekStart
            ? focus.minus({ days: (focus.weekday - weekStart + 7) % 7 }).startOf("day")
            : getStartOfLocalWeek(focus);
        return { start, stop: start.plus({ weeks: 1 }) };
    }
    if (scale === "year") {
        start = focus.startOf("year");
        return { start, stop: start.plus({ years: 1 }) };
    }
    start = focus.startOf("month");
    return { start, stop: start.plus({ months: 1 }) };
}

/** Avance (ou recule si n < 0) la date de référence d'une période. */
export function shiftFocus(scale, focus, n) {
    const key = { day: "days", week: "weeks", month: "months", year: "years" }[scale];
    return focus.plus({ [key]: n });
}

/** Colonnes de la période : une par heure, jour ou mois. */
export function getColumns(scale, range) {
    const unit = COLUMN_UNIT[scale];
    const now = DateTime.local();
    const columns = [];
    let cursor = range.start;
    while (cursor < range.stop) {
        const next = cursor.plus({ [`${unit}s`]: 1 });
        let label;
        let sublabel = "";
        if (unit === "hour") {
            label = cursor.toFormat("HH");
        } else if (unit === "day") {
            label = cursor.toFormat("d");
            sublabel = cursor.toFormat("ccc");
        } else {
            label = cursor.toFormat("LLL");
        }
        columns.push({
            index: columns.length,
            start: cursor,
            stop: next,
            label,
            sublabel,
            isToday: now >= cursor && now < next,
        });
        cursor = next;
    }
    return columns;
}

/** Titre de la période (« novembre 2026 », « 9 – 15 nov. 2026 »…). */
export function getRangeTitle(scale, range) {
    const last = range.stop.minus({ days: 1 });
    if (scale === "day") {
        return range.start.toFormat("cccc d LLLL yyyy");
    }
    if (scale === "week") {
        if (range.start.month === last.month) {
            return `${range.start.toFormat("d")} – ${last.toFormat("d LLLL yyyy")}`;
        }
        return `${range.start.toFormat("d LLL")} – ${last.toFormat("d LLL yyyy")}`;
    }
    if (scale === "year") {
        return range.start.toFormat("yyyy");
    }
    return range.start.toFormat("LLLL yyyy");
}

/**
 * Colonnes [first, last + 1[ couvertes par l'intervalle [start, stop[, ou null
 * s'il est hors période. `stop` est exclusif.
 */
export function columnSpan(columns, start, stop) {
    if (!columns.length || stop <= columns[0].start || start >= columns.at(-1).stop) {
        return null;
    }
    let first = 0;
    while (first < columns.length - 1 && columns[first].stop <= start) {
        first++;
    }
    let last = first;
    while (last < columns.length - 1 && columns[last + 1].start < stop) {
        last++;
    }
    return { first, last: last + 1 };
}

/** Fusionne des intervalles {start, stop} (DateTime) qui se touchent. */
export function mergeIntervals(intervals) {
    const sorted = [...intervals].sort((a, b) => a.start - b.start);
    const merged = [];
    for (const interval of sorted) {
        const previous = merged.at(-1);
        if (previous && interval.start <= previous.stop) {
            if (interval.stop > previous.stop) {
                previous.stop = interval.stop;
            }
        } else {
            merged.push({ ...interval });
        }
    }
    return merged;
}

/**
 * Répartit des barres sur des niveaux pour qu'elles ne se chevauchent pas :
 * chaque barre prend le premier niveau libre. Renvoie le nombre de niveaux.
 */
export function assignLevels(pills) {
    const levelEnds = [];
    pills.sort((a, b) => a.first - b.first || b.last - a.last || a.id - b.id);
    for (const pill of pills) {
        let level = levelEnds.findIndex((end) => end <= pill.first);
        if (level === -1) {
            level = levelEnds.length;
            levelEnds.push(pill.last);
        } else {
            levelEnds[level] = pill.last;
        }
        pill.level = level;
    }
    return levelEnds.length;
}

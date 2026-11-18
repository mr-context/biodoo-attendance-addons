/** Durée courte : 8 → « 8h », 7,5 → « 7h30 », 0,25 → « 15m ». */
export function shortHours(value) {
    const minutes = Math.round((value || 0) * 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (!h) {
        return `${m}m`;
    }
    return m ? `${h}h${String(m).padStart(2, "0")}` : `${h}h`;
}

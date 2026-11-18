import { Component, useExternalListener, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { usePopover } from "@web/core/popover/popover_hook";
import { useService } from "@web/core/utils/hooks";
import { formatFloat } from "@web/views/fields/formatters";
import { FormViewDialog } from "@web/views/view_dialogs/form_view_dialog";
import { GanttPopover } from "./gantt_popover";

/**
 * Affichage : une grille dont la première colonne porte le nom des lignes et
 * les suivantes les heures, jours ou mois de la période.
 *
 * Points d'extension pour les modules métier :
 *   static rowHeaderTemplate  en-tête de ligne (ex. avatar de l'employé)
 *   getPillLabel(pill)        libellé d'une barre
 *   getAggregateLabel(value)  libellé des lignes de groupe et de total
 *   getPopoverExtraLines(pill) lignes ajoutées à la bulle
 *   getPopoverTitle(pill)     titre de la bulle (popoverShowsPeriod : ligne « Période »)
 *   getRowTotalLabel(row)     total en tête de ligne ; getLegendCountLabel(entry)
 *   staticLegend / legendHint légende fixe (couleurs, coin) et astuce
 *   rootClass                 classe CSS d'un module métier
 *   static selectionBarTemplate barre d'actions de la sélection de cases
 *   get selectionEnabled / onSelectionDone(selection)  sélection de cases
 *
 * Souris (si la vue le permet) :
 *   glisser une barre      la déplace (et la change de ligne)
 *   tirer son bord droit   l'étire ou la raccourcit
 *   glisser sur des cases  crée un enregistrement sur la période choisie
 *                          (ou, pour un module métier, sélectionne des cases)
 */
export class GanttRenderer extends Component {
    static template = "web_dz_gantt.GanttRenderer";
    static rowHeaderTemplate = "web_dz_gantt.GanttRowHeader";
    static selectionBarTemplate = null;
    static components = {};
    static props = {
        model: Object,
        // Change à chaque chargement : force le rendu quand la recherche ou
        // le regroupement changent (le modèle, lui, reste le même objet).
        data: Object,
    };

    setup() {
        this.dialog = useService("dialog");
        this.popover = usePopover(GanttPopover, { position: "bottom" });
        // drag : barre déplacée ; resize : barre étirée ; selection : cases.
        this.interaction = useState({ drag: null, resize: null, selection: null });
        // Légende : valeur mise en avant (les autres barres s'estompent).
        this.legendState = useState({ focus: null });
        this.pending = null;
        this.justMoved = false;
        useExternalListener(window, "pointermove", (ev) => this.onPointerMove(ev));
        useExternalListener(window, "pointerup", (ev) => this.onPointerUp(ev));
        useExternalListener(window, "keydown", (ev) => {
            if (ev.key === "Escape") {
                this.cancelInteractions();
            }
        });
    }

    get model() {
        return this.props.model;
    }

    get metaData() {
        return this.model.metaData;
    }

    get rowHeaderTemplate() {
        return this.constructor.rowHeaderTemplate;
    }

    get gridStyle() {
        const columns = this.metaData.columns.length;
        const min = { day: 2.5, week: 6, month: 2.25, year: 4 }[this.metaData.scale];
        return `--dz-gantt-columns: ${columns}; --dz-gantt-cell-min: ${min}rem;`;
    }

    /**
     * Lignes visibles, à plat, dans l'ordre d'affichage. Les lignes simples
     * sont numérotées en alternance (`striped`) pour qu'on suive une ligne à
     * l'œil ; la numérotation repart sous chaque groupe.
     */
    get visibleRows() {
        const rows = [];
        const visit = (list) => {
            let index = 0;
            for (const row of list) {
                row.striped = !row.isGroup && index++ % 2 === 1;
                rows.push(row);
                if (row.isGroup && !this.metaData.closedRows.has(row.id)) {
                    visit(row.rows);
                }
            }
        };
        visit(this.model.data.rows);
        return rows;
    }

    isClosed(row) {
        return this.metaData.closedRows.has(row.id);
    }

    // ── Contenu ────────────────────────────────────────────────────────────

    getPillLabel(pill) {
        return pill.label;
    }

    getPillTitle(pill) {
        return this.getPillLabel(pill);
    }

    getAggregateLabel(value) {
        if (this.metaData.consolidationField) {
            return formatFloat(value, { digits: [false, 2] });
        }
        return String(value);
    }

    getPopoverExtraLines(/* pill */) {
        return [];
    }

    /** La bulle affiche-t-elle la ligne « Période » ? */
    get popoverShowsPeriod() {
        return true;
    }

    /** Titre de la bulle (par défaut, le nom de l'enregistrement). */
    getPopoverTitle(pill) {
        return pill.record.display_name || "";
    }

    pillStyle(pill) {
        let { first, last } = pill;
        const n = this.metaData.columns.length;
        const { drag, resize } = this.interaction;
        if (drag && drag.pillId === pill.id) {
            const width = last - first;
            first = Math.min(Math.max(first + drag.delta, 0), n - 1);
            last = Math.min(first + width, n);
        } else if (resize && resize.pillId === pill.id) {
            last = Math.min(Math.max(last + resize.delta, first + 1), n);
        }
        return `grid-column: ${first + 1} / ${last + 1}; grid-row: ${pill.level + 1};`;
    }

    pillClass(pill) {
        const classes = [`o_dz_gantt_color_${pill.colorIndex}`, ...pill.decorations];
        if (this.canDrag) {
            classes.push("o_dz_gantt_pill_draggable");
        }
        if (this.legendState.focus !== null && this.model.legendKey(pill) !== this.legendState.focus) {
            classes.push("o_dz_gantt_pill_dimmed");
        }
        if (this.interaction.drag?.pillId === pill.id || this.interaction.resize?.pillId === pill.id) {
            classes.push("o_dz_gantt_pill_moving");
        }
        if (pill.start < this.metaData.columns[0].start) {
            classes.push("o_dz_gantt_pill_cut_start");
        }
        if (pill.stop > this.metaData.columns.at(-1).stop) {
            classes.push("o_dz_gantt_pill_cut_stop");
        }
        return classes.join(" ");
    }

    segmentStyle(segment) {
        return `grid-column: ${segment.first + 1} / ${segment.last + 1};`;
    }

    cellClass(row, column, selection = this.getSelection()) {
        return {
            o_dz_gantt_today: column.isToday,
            o_dz_gantt_unavailable: row.unavailable?.has(column.index),
            o_dz_gantt_cell_selected: this.isCellSelected(row, column.index, selection),
        };
    }

    // ── Info-bulles : chaque élément s'explique au survol ──────────────────

    get todayTitle() {
        return _t("Aujourd'hui");
    }

    get groupTitle() {
        return _t("Cliquer pour replier ou déplier. Les traits donnent le total de la ligne par %s.",
                  this.columnUnitLabel);
    }

    get totalTitle() {
        return _t("Total de toutes les lignes affichées, par %s", this.columnUnitLabel);
    }

    get columnUnitLabel() {
        return { day: _t("heure"), week: _t("jour"), month: _t("jour"), year: _t("mois") }[this.metaData.scale];
    }

    cellTitle(row, column) {
        return row.unavailable?.has(column.index) ? _t("Hors planning (repos, férié ou congé)") : "";
    }

    aggregateTitle(segment) {
        const { columns } = this.metaData;
        const value = this.getAggregateLabel(segment.value);
        if (segment.last - segment.first <= 1) {
            return _t("%(value)s le %(day)s", { value, day: columns[segment.first].start.toFormat("dd/MM") });
        }
        return _t("%(value)s par %(unit)s, du %(from)s au %(to)s", {
            value,
            unit: this.columnUnitLabel,
            from: columns[segment.first].start.toFormat("dd/MM"),
            to: columns[segment.last - 1].start.toFormat("dd/MM"),
        });
    }

    totalCellTitle(segment) {
        const column = this.metaData.columns[segment.first];
        const when = this.metaData.scale === "day" ? column.start.toFormat("HH:mm") : column.start.toFormat("dd/MM");
        return _t("Total %(when)s : %(value)s", { when, value: this.getAggregateLabel(segment.value) });
    }

    rowClass(row) {
        return {
            o_dz_gantt_row_group: row.isGroup,
            o_dz_gantt_row_closed: row.isGroup && this.isClosed(row),
            o_dz_gantt_row_striped: row.striped,
            o_dz_gantt_row_drop_target:
                this.interaction.drag && this.interaction.drag.targetRowId === row.id &&
                this.interaction.drag.targetRowId !== this.interaction.drag.rowId,
            o_dz_gantt_row_selectable: !row.isGroup && (this.selectionEnabled || this.metaData.canCreate),
        };
    }

    // ── Souris : déplacer, étirer, sélectionner ────────────────────────────

    get canDrag() {
        const { canEdit, disableDragDrop } = this.metaData;
        return canEdit && !disableDragDrop && this.model.canReschedule();
    }

    get canResize() {
        return this.canDrag && this.metaData.dateStartField !== this.metaData.dateStopField;
    }

    /** Sélection de cases à la souris : création par défaut si permise. */
    get selectionEnabled() {
        return this.metaData.canCreate;
    }

    columnAt(clientX, cellsEl) {
        const rect = cellsEl.getBoundingClientRect();
        const n = this.metaData.columns.length;
        const index = Math.floor(((clientX - rect.left) / rect.width) * n);
        return Math.min(Math.max(index, 0), n - 1);
    }

    /**
     * Toutes les lignes par identifiant (aussi celles des groupes repliés).
     * Mémorisé par chargement : sert à chaque mouvement de souris.
     */
    get rowsById() {
        if (this._rowsByIdData === this.model.data) {
            return this._rowsById;
        }
        const byId = {};
        const visit = (rows) => {
            for (const row of rows) {
                byId[row.id] = row;
                visit(row.rows || []);
            }
        };
        visit(this.model.data.rows);
        this._rowsByIdData = this.model.data;
        this._rowsById = byId;
        return byId;
    }

    rowAt(clientX, clientY) {
        const el = document.elementFromPoint(clientX, clientY)?.closest(".o_dz_gantt_row[data-row-id]");
        const row = el && this.rowsById[el.dataset.rowId];
        return row && !row.isGroup ? { row, cellsEl: el.querySelector(".o_dz_gantt_cells") } : null;
    }

    onPillPointerDown(ev, pill, row) {
        if (ev.button !== 0 || !this.canDrag) {
            return;
        }
        ev.stopPropagation();
        const cellsEl = ev.currentTarget.closest(".o_dz_gantt_cells");
        this.pending = { type: "drag", pill, row, cellsEl, x: ev.clientX, y: ev.clientY,
                         startCol: this.columnAt(ev.clientX, cellsEl) };
    }

    onResizePointerDown(ev, pill, row) {
        if (ev.button !== 0) {
            return;
        }
        ev.stopPropagation();
        const cellsEl = ev.currentTarget.closest(".o_dz_gantt_cells");
        this.pending = { type: "resize", pill, row, cellsEl, x: ev.clientX, y: ev.clientY,
                         startCol: this.columnAt(ev.clientX, cellsEl) };
    }

    onCellsPointerDown(ev, row) {
        // Une barre déplaçable garde son glisser ; sinon la sélection peut
        // partir d'une case déjà occupée (cas courant : remplacer une absence).
        const onPill = Boolean(ev.target.closest(".o_dz_gantt_pill"));
        if (ev.button !== 0 || row.isGroup || !this.selectionEnabled || (onPill && this.canDrag)) {
            return;
        }
        const col = this.columnAt(ev.clientX, ev.currentTarget);
        this.interaction.selection = {
            anchorRowId: row.id, rowId: row.id, anchorCol: col, col, active: true, onPill,
            moved: false,
        };
    }

    onPointerMove(ev) {
        const { pending, interaction } = this;
        if (pending && !interaction.drag && !interaction.resize) {
            if (Math.abs(ev.clientX - pending.x) + Math.abs(ev.clientY - pending.y) < 5) {
                return;
            }
            this.popover.close();
            if (pending.type === "drag") {
                interaction.drag = { pillId: pending.pill.id, rowId: pending.row.id, delta: 0,
                                     targetRowId: pending.row.id };
            } else {
                interaction.resize = { pillId: pending.pill.id, delta: 0 };
            }
        }
        if (interaction.drag) {
            interaction.drag.delta = this.columnAt(ev.clientX, pending.cellsEl) - pending.startCol;
            const hit = this.rowAt(ev.clientX, ev.clientY);
            if (hit) {
                interaction.drag.targetRowId = hit.row.id;
            }
        } else if (interaction.resize) {
            interaction.resize.delta = this.columnAt(ev.clientX, pending.cellsEl) - pending.startCol;
        } else if (interaction.selection?.active) {
            const hit = this.rowAt(ev.clientX, ev.clientY);
            if (hit) {
                const col = this.columnAt(ev.clientX, hit.cellsEl);
                const sel = interaction.selection;
                if (hit.row.id !== sel.rowId || col !== sel.col) {
                    sel.moved = true;
                    this.popover.close();
                }
                sel.rowId = hit.row.id;
                sel.col = col;
            }
        }
    }

    async onPointerUp() {
        const { pending, interaction } = this;
        this.pending = null;
        if (interaction.drag) {
            const { delta, targetRowId } = interaction.drag;
            const target = this.rowsById[targetRowId];
            this.markMoved();
            try {
                if (delta || targetRowId !== pending.row.id) {
                    await this.model.reschedule(pending.pill, delta, pending.row, target);
                }
            } finally {
                interaction.drag = null;
            }
        } else if (interaction.resize) {
            const { delta } = interaction.resize;
            this.markMoved();
            try {
                await this.model.resize(pending.pill, delta);
            } finally {
                interaction.resize = null;
            }
        } else if (interaction.selection?.active) {
            const sel = interaction.selection;
            if (sel.onPill && !sel.moved) {
                // Simple clic sur une barre : c'est la bulle qui s'ouvre.
                interaction.selection = null;
                return;
            }
            sel.active = false;
            this.popover.close();
            this.onSelectionDone(this.getSelection());
        }
    }

    /** Le clic qui suit un glisser n'ouvre pas la bulle. */
    markMoved() {
        this.justMoved = true;
        setTimeout(() => (this.justMoved = false), 100);
    }

    cancelInteractions() {
        this.pending = null;
        Object.assign(this.interaction, { drag: null, resize: null, selection: null });
    }

    /**
     * Cases sélectionnées : { rows: [lignes], first, last } (last exclusif).
     * Le rectangle va de la case de départ à la case sous la souris.
     */
    getSelection() {
        const sel = this.interaction.selection;
        if (!sel) {
            return null;
        }
        const leaves = this.visibleRows.filter((row) => !row.isGroup);
        const a = leaves.findIndex((row) => row.id === sel.anchorRowId);
        const b = leaves.findIndex((row) => row.id === sel.rowId);
        if (a === -1 || b === -1) {
            return null;
        }
        const rows = leaves.slice(Math.min(a, b), Math.max(a, b) + 1);
        return {
            rows,
            rowIds: new Set(rows.map((row) => row.id)),
            first: Math.min(sel.anchorCol, sel.col),
            last: Math.max(sel.anchorCol, sel.col) + 1,
        };
    }

    /** `selection` : celle du rendu en cours (calculée une fois, pas par case). */
    isCellSelected(row, index, selection) {
        if (!selection || row.isGroup) {
            return false;
        }
        return index >= selection.first && index < selection.last && selection.rowIds.has(row.id);
    }

    get hasSelectionBar() {
        return Boolean(this.constructor.selectionBarTemplate && this.interaction.selection &&
                       !this.interaction.selection.active);
    }

    get selectionBarTemplate() {
        return this.constructor.selectionBarTemplate;
    }

    /** Fin de sélection : par défaut, création sur la ligne et la période choisies. */
    onSelectionDone(selection) {
        if (!selection || !this.metaData.canCreate) {
            this.interaction.selection = null;
            return;
        }
        const row = selection.rows[0];
        this.openCreate(row, selection.first, selection.last);
        this.interaction.selection = null;
    }

    openCreate(row, first, last) {
        const { resModel, formViewId } = this.metaData;
        this.dialog.add(FormViewDialog, {
            resModel,
            viewId: formViewId || false,
            context: this.model.createContext(row, first, last),
            title: _t("Créer"),
            onRecordSaved: () => this.model.reload(),
        });
    }

    /**
     * Légende fixe (couleur → sens) quand la vue n'a pas de champ de légende :
     * [{ colorIndex, label }]. À redéfinir.
     */
    get staticLegend() {
        return [];
    }

    /** Libellé du total d'une entrée de légende. */
    getLegendTotal(entry) {
        return this.metaData.consolidationField ? this.getAggregateLabel(entry.total) : String(entry.count);
    }

    /** Compteur d'une entrée de légende, libellé (« 12 · 96h » ne dit rien). */
    getLegendCountLabel(entry) {
        if (!this.metaData.consolidationField) {
            return _t("%s élément(s)", entry.count);
        }
        return _t("%(count)s élément(s) · %(total)s", { count: entry.count, total: this.getLegendTotal(entry) });
    }

    /** Astuce affichée dans la légende (actions possibles). À redéfinir. */
    get legendHint() {
        return this.metaData.canCreate ? _t("Glissez sur des cases vides pour créer") : "";
    }

    /** Total d'une ligne sur la période affichée, en tête de ligne. */
    getRowTotalLabel(row) {
        return row.total ? this.getAggregateLabel(row.total) : "";
    }

    get rowTotalTitle() {
        return _t("Total de la ligne sur la période affichée");
    }

    /** Classe CSS ajoutée à la racine (thème d'un module métier). */
    get rootClass() {
        return "";
    }

    toggleLegend(entry) {
        this.legendState.focus = this.legendState.focus === entry.key ? null : entry.key;
    }

    maxValue(segments) {
        return Math.max(1, ...segments.map((s) => s.value));
    }

    progressPercent(row) {
        const bar = row.progressBar;
        if (!bar || !bar.max_value) {
            return 0;
        }
        return Math.min(100, Math.round((100 * bar.value) / bar.max_value));
    }

    // ── Actions ────────────────────────────────────────────────────────────

    onPillClick(ev, pill) {
        if (this.interaction.selection) {
            return;
        }
        if (this.justMoved) {
            this.justMoved = false;
            return;
        }
        const { fields, popoverFields, dateStartField, dateStopField, canEdit, canDelete } =
            this.metaData;
        this.popover.open(ev.currentTarget, {
            pill,
            fields,
            popoverFields,
            dateStartField,
            dateStopField,
            canEdit,
            canDelete,
            onOpen: (p) => this.openRecord(p.id),
            onDelete: (p) => this.deleteRecord(p.id),
            extraLines: this.getPopoverExtraLines(pill),
            title: this.getPopoverTitle(pill),
            showPeriod: this.popoverShowsPeriod,
        });
    }

    openRecord(resId) {
        const { resModel, formViewId, canEdit, context } = this.metaData;
        this.dialog.add(FormViewDialog, {
            resModel,
            resId,
            viewId: formViewId || false,
            context,
            preventEdit: !canEdit,
            title: _t("Ouvrir"),
            onRecordSaved: () => this.model.reload(),
        });
    }

    deleteRecord(resId) {
        this.dialog.add(ConfirmationDialog, {
            body: _t("Supprimer cet enregistrement ?"),
            confirmLabel: _t("Supprimer"),
            confirm: () => this.model.unlinkRecords([resId]),
            cancel: () => {},
        });
    }
}

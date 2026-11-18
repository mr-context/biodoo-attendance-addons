import { Component, useRef } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useModel } from "@web/model/model";
import { useBus } from "@web/core/utils/hooks";
import { ActionHelper } from "@web/views/action_helper";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { Pager } from "@web/core/pager/pager";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CogMenu } from "@web/search/cog_menu/cog_menu";
import { Layout } from "@web/search/layout";
import { SearchBar } from "@web/search/search_bar/search_bar";
import { useSearchBarToggler } from "@web/search/search_bar/search_bar_toggler";
import { useSetupAction } from "@web/search/action_hook";
import { standardViewProps } from "@web/views/standard_view_props";

export const SCALE_LABELS = {
    day: _t("Jour"),
    week: _t("Semaine"),
    month: _t("Mois"),
    year: _t("Année"),
};

export class GanttController extends Component {
    static template = "web_dz_gantt.GanttController";
    static components = { Layout, SearchBar, CogMenu, ActionHelper, Dropdown, DropdownItem, Pager };
    static props = {
        ...standardViewProps,
        Model: Function,
        modelParams: Object,
        Renderer: Function,
        buttonTemplate: String,
    };

    setup() {
        this.model = useModel(this.props.Model, this.props.modelParams);
        // Navigation, échelle, groupes repliés : le modèle prévient, on redessine.
        useBus(this.model.bus, "update", () => this.render(true));
        useSetupAction({
            rootRef: useRef("root"),
            getLocalState: () => ({
                metaData: {
                    scale: this.model.metaData.scale,
                    focusDate: this.model.metaData.focusDate,
                    closedRows: [...this.model.metaData.closedRows],
                },
            }),
        });
        this.searchBarToggler = useSearchBarToggler();
        this.scaleLabels = SCALE_LABELS;
    }

    get hasGroups() {
        return Boolean(this.model.data?.rows.some((row) => row.isGroup));
    }

    /** Pager des lignes, affiché seulement s'il y a plus d'une page. */
    get pagerProps() {
        const { offset, limit } = this.model.metaData;
        const total = this.model.data?.length || 0;
        if (!this.model.metaData.groupBy?.length || total <= limit) {
            return null;
        }
        return { offset, limit, total, onUpdate: (params) => this.model.setPage(params) };
    }

    get showNoContentHelp() {
        return !this.model.hasData();
    }
}

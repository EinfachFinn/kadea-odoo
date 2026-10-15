// "Wissen (n)"-Button in JEDER Formularansicht: zeigt alle Wissensseiten, die den Datensatz per @ erwähnen.
import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";
import { FormController } from "@web/views/form/form_controller";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useService } from "@web/core/utils/hooks";
import { user } from "@web/core/user";
import { relTime } from "./collab";

export class WissenFormButton extends Component {
    static template = "wissensmanagement.FormButton";
    static components = { Dropdown, DropdownItem };
    static props = { resModel: String, resId: { type: [Number, Boolean], optional: true }, recordName: { type: String, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ pages: [], allowed: false });
        this.relTime = relTime;
        onWillStart(async () => {
            this.state.allowed = await user.hasGroup("wissensmanagement.group_reader");
            await this.load(this.props);
        });
        onWillUpdateProps((next) => {
            if (next.resId !== this.props.resId || next.resModel !== this.props.resModel) {
                return this.load(next);
            }
        });
    }

    async load(props) {
        if (!this.state.allowed || !props.resId || props.resModel.startsWith("wissen.")) {
            this.state.pages = [];
            return;
        }
        try {
            this.state.pages = await this.orm.silent.call("wissen.vault", "record_pages", [props.resModel, props.resId]);
        } catch {
            this.state.pages = [];
        }
    }

    open(path) {
        this.action.doAction({
            type: "ir.actions.client",
            tag: "wissensmanagement.app",
            name: "Wissensmanagement",
            context: { wm_path: path },
        });
    }
}

FormController.components = { ...FormController.components, WissenFormButton };

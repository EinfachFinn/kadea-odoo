/** @odoo-module **/
import {SocialKanbanRecord} from "@social_media_base/js/views/kanban/social_kanban_record.esm";
import {patch} from "@web/core/utils/patch";
import {useService} from "@web/core/utils/hooks";

patch(SocialKanbanRecord.prototype, {
    /**
     * Instagram does not allow liking through the API.
     *
     * @override
     */
    setup() {
        super.setup();
        this.socialInstagramOrm = useService("orm");
        this.socialInstagramAction = useService("action");
        this.record.notAvailableLike = [
            ...(this.record.notAvailableLike || []),
            "instagram",
        ];
    },

    _isInstagramManual() {
        return (
            this.record.media_type?.raw_value === "instagram" &&
            this.record.state?.raw_value === "manual"
        );
    },

    /**
     * A planned Instagram publication has no address yet: its card opens the
     * publication, where it is marked as published, instead of telling the
     * user that the post does not exist.
     *
     * @override
     */
    async onGlobalClick(ev) {
        if (
            this._isInstagramManual() &&
            ev.target.closest("div.oe_kanban_social_dashboard")
        ) {
            ev.stopPropagation();
            return this.socialInstagramAction.doAction({
                type: "ir.actions.act_window",
                res_model: "social.post.account",
                res_id: this.record.id.raw_value,
                views: [[false, "form"]],
                target: "current",
            });
        }
        return super.onGlobalClick(ev);
    },

    /**
     * @override
     */
    async validPostExist() {
        const res = super.validPostExist();
        if (this.record.media_type?.raw_value === "instagram") {
            return await this.socialInstagramOrm.call(
                "social.post.account",
                "get_post_instagram",
                [this.record.id.raw_value]
            );
        }
        return res;
    },
});

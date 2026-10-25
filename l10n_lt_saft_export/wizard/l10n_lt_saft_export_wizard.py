from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import BinaryBytes

from ..models.l10n_lt_saft_export import DATA_TYPES


class L10nLtSaftExportWizard(models.TransientModel):
    _name = "l10n_lt.saft.export.wizard"
    _description = "Lithuanian SAF-T export"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    date_from = fields.Date(
        required=True, default=lambda self: self._default_date_from()
    )
    date_to = fields.Date(required=True, default=lambda self: self._default_date_to())
    data_type = fields.Selection(DATA_TYPES, required=True, default="F")
    tax_accounting_basis = fields.Selection(
        selection=[("K", "Accrual (K)"), ("P", "Cash (P)")],
        required=True,
        default=lambda self: self.env.company.l10n_lt_saft_tax_accounting_basis or "K",
    )
    entity = fields.Char(
        default=lambda self: self.env.company.l10n_lt_saft_entity or "ODOO"
    )
    validate_xsd = fields.Boolean(string="Validate against XSD", default=True)
    state = fields.Selection([("draft", "Draft"), ("done", "Done")], default="draft")
    file_data = fields.Binary(string="SAF-T File", readonly=True, attachment=False)
    file_name = fields.Char(readonly=True)

    @api.model
    def _default_date_from(self):
        today = fields.Date.context_today(self)
        return self.env.company.compute_fiscalyear_dates(
            today - relativedelta(years=1)
        )["date_from"]

    @api.model
    def _default_date_to(self):
        today = fields.Date.context_today(self)
        return self.env.company.compute_fiscalyear_dates(
            today - relativedelta(years=1)
        )["date_to"]

    def action_generate(self):
        self.ensure_one()
        exporter = self.env["l10n_lt.saft.export"]
        options = exporter._saft_get_options(
            self.company_id,
            self.date_from,
            self.date_to,
            data_type=self.data_type,
            tax_accounting_basis=self.tax_accounting_basis,
            entity=self.entity,
        )
        content = exporter._saft_generate(options)
        if self.validate_xsd:
            errors = exporter._saft_validate(content)
            if errors:
                raise UserError(
                    self.env._(
                        "The generated file does not match the SAF-T XSD 2.01:\n%s",
                        "\n".join(errors[:30]),
                    )
                )
        file_name = exporter._saft_get_filename(options)
        self.write(
            {
                "state": "done",
                "file_data": BinaryBytes(content, filename=file_name),
                "file_name": file_name,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("SAF-T Export (Lithuania)"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

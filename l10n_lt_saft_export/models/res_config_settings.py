from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    l10n_lt_saft_entity = fields.Char(
        related="company_id.l10n_lt_saft_entity", readonly=False
    )
    l10n_lt_saft_tax_accounting_basis = fields.Selection(
        related="company_id.l10n_lt_saft_tax_accounting_basis", readonly=False
    )
    l10n_lt_saft_contact_id = fields.Many2one(
        related="company_id.l10n_lt_saft_contact_id", readonly=False
    )

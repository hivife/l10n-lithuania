from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    l10n_lt_saft_entity = fields.Char(
        string="SAF-T Entity",
        default="ODOO",
        help="Identifier of the unit / information system the SAF-T file is "
        "generated from (Header/Entity). Only capital letters, digits and "
        "underscores, max. 20 characters.",
    )
    l10n_lt_saft_tax_accounting_basis = fields.Selection(
        selection=[("K", "Accrual (K)"), ("P", "Cash (P)")],
        string="SAF-T Tax Accounting Basis",
        default="K",
    )
    l10n_lt_saft_contact_id = fields.Many2one(
        comodel_name="res.partner",
        string="SAF-T Contact Person",
        help="Representative of the company written in the SAF-T header. "
        "The company phone is used when the contact has none.",
    )
    l10n_lt_saft_owner_ids = fields.One2many(
        comodel_name="l10n_lt.saft.owner",
        inverse_name="company_id",
        string="SAF-T Owners",
    )

from odoo import fields, models


class L10nLtSaftOwner(models.Model):
    """Owners / shareholders of the company (MasterFiles/Owners)."""

    _name = "l10n_lt.saft.owner"
    _description = "SAF-T Owner (Lithuania)"
    _order = "company_id, id"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    partner_id = fields.Many2one("res.partner", string="Owner", required=True)
    owner_code = fields.Char(
        size=35,
        help="Legal entity code or personal identifier of the owner. "
        "The company ID or tax ID of the partner is used when empty.",
    )
    account_id = fields.Many2one(
        "account.account", string="Capital Account", required=True, check_company=True
    )
    shares_quantity = fields.Float(string="Number of Shares", digits=(16, 4))
    shares_amount = fields.Monetary()
    shares_type = fields.Selection(
        selection=[("PP", "PP - Ordinary shares"), ("PRV", "PRV - Preference shares")],
    )
    shares_acquisition_date = fields.Date(string="Acquisition Date")
    shares_transfer_date = fields.Date(string="Transfer Date")

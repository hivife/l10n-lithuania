from odoo import api, fields, models

# Odoo account type -> SAF-T AccountType (IT non-current assets, TT current assets,
# NK equity, I liabilities, P income, S costs, KT other)
ACCOUNT_TYPE_MAPPING = {
    "asset_non_current": "IT",
    "asset_fixed": "IT",
    "asset_receivable": "TT",
    "asset_cash": "TT",
    "asset_current": "TT",
    "asset_prepayments": "TT",
    "equity": "NK",
    "equity_unaffected": "NK",
    "liability_payable": "I",
    "liability_credit_card": "I",
    "liability_current": "I",
    "liability_non_current": "I",
    "income": "P",
    "income_other": "P",
    "expense": "S",
    "expense_other": "S",
    "expense_depreciation": "S",
    "expense_direct_cost": "S",
    "off_balance": "KT",
}


class AccountAccount(models.Model):
    _inherit = "account.account"

    l10n_lt_saft_account_type = fields.Selection(
        selection=[
            ("IT", "IT - Non-current assets"),
            ("TT", "TT - Current assets"),
            ("NK", "NK - Equity"),
            ("I", "I - Liabilities"),
            ("P", "P - Income"),
            ("S", "S - Costs"),
            ("KT", "KT - Other"),
        ],
        string="SAF-T Account Type",
        compute="_compute_l10n_lt_saft_account_type",
        store=True,
        readonly=False,
    )
    l10n_lt_saft_account_table_id = fields.Char(
        string="SAF-T Classifier Code",
        help="Account code according to the VMI account classifiers No. 1 - No. 3 "
        "(Annex 2 of the SAF-T description). The account code is used when empty.",
    )
    l10n_lt_saft_account_table_description = fields.Char(
        string="SAF-T Classifier Name",
        help="Account name according to the VMI account classifiers. "
        "The account name is used when empty.",
    )

    @api.depends("account_type")
    def _compute_l10n_lt_saft_account_type(self):
        for account in self:
            account.l10n_lt_saft_account_type = ACCOUNT_TYPE_MAPPING.get(
                account.account_type, "KT"
            )

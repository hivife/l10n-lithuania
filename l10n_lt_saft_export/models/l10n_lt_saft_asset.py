from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_round


class L10nLtSaftAsset(models.Model):
    """Minimal fixed asset register used to fill the SAF-T parts MasterFiles/Assets and
    SourceDocuments/AssetTransactions (Odoo Community has no asset management)."""

    _name = "l10n_lt.saft.asset"
    _description = "SAF-T Asset (Lithuania)"
    _order = "code, id"
    _check_company_auto = True

    name = fields.Char(string="Description", required=True)
    code = fields.Char(string="Inventory Number", required=True, size=35)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    active = fields.Boolean(default=True)
    account_id = fields.Many2one(
        "account.account",
        string="Asset Account",
        required=True,
        check_company=True,
        help="Account holding the acquisition cost of the asset.",
    )
    depreciation_account_id = fields.Many2one(
        "account.account", string="Accumulated Depreciation Account", check_company=True
    )
    registration_number = fields.Char(
        string="Registration ID",
        size=70,
        help="Registration details of assets subject to mandatory registration, "
        "e.g. real property number.",
    )
    valuation_class = fields.Char(size=18)
    partner_id = fields.Many2one("res.partner", string="Supplier")
    acquisition_move_id = fields.Many2one(
        "account.move",
        string="Acquisition Entry",
        check_company=True,
        domain="[('state', '=', 'posted')]",
    )
    acquisition_date = fields.Date(required=True)
    start_date = fields.Date(
        string="Commissioning Date",
        required=True,
        help="Depreciation starts on the first day of the month following this date.",
    )
    acquisition_value = fields.Monetary(string="Acquisition Cost", required=True)
    useful_life_months = fields.Integer(
        string="Useful Life (months)", required=True, default=60
    )
    disposal_date = fields.Date()
    disposal_move_id = fields.Many2one(
        "account.move",
        string="Disposal Entry",
        check_company=True,
        domain="[('state', '=', 'posted')]",
    )

    _code_company_uniq = models.Constraint(
        "unique(code, company_id)",
        "The inventory number must be unique per company.",
    )

    @api.constrains(
        "acquisition_date",
        "start_date",
        "disposal_date",
        "useful_life_months",
        "acquisition_value",
    )
    def _check_values(self):
        for asset in self:
            if asset.start_date < asset.acquisition_date:
                raise ValidationError(
                    self.env._(
                        "The commissioning date cannot be before the acquisition date."
                    )
                )
            if asset.disposal_date and asset.disposal_date < asset.start_date:
                raise ValidationError(
                    self.env._(
                        "The disposal date cannot be before the commissioning date."
                    )
                )
            if asset.useful_life_months <= 0:
                raise ValidationError(self.env._("The useful life must be positive."))
            if asset.acquisition_value < 0:
                raise ValidationError(
                    self.env._("The acquisition cost cannot be negative.")
                )

    # -------------------------------------------------------------------------
    # Depreciation (straight-line, monthly, starting the month after commissioning)
    # -------------------------------------------------------------------------

    @staticmethod
    def _month_index(date):
        return date.year * 12 + date.month

    def _get_depreciated_months(self, date):
        """Number of months depreciated up to and including the month of ``date``."""
        self.ensure_one()
        if not date:
            return 0
        end = date
        if self.disposal_date and self.disposal_date < end:
            end = self.disposal_date
        months = self._month_index(end) - self._month_index(self.start_date)
        return max(0, min(months, self.useful_life_months))

    def _get_accumulated_depreciation(self, date):
        """Accumulated depreciation at the end of the month of ``date``."""
        self.ensure_one()
        if not date or date < self.acquisition_date:
            return 0.0
        months = self._get_depreciated_months(date)
        if months >= self.useful_life_months:
            return self.acquisition_value
        monthly = float_round(
            self.acquisition_value / self.useful_life_months, precision_digits=2
        )
        return min(
            self.acquisition_value, float_round(monthly * months, precision_digits=2)
        )

    def _is_owned_at(self, date):
        self.ensure_one()
        return self.acquisition_date <= date and not (
            self.disposal_date and self.disposal_date <= date
        )

    def _get_saft_period_values(self, date_from, date_to):
        """Amounts for MasterFiles/Assets/Asset/Valuations for the given period."""
        self.ensure_one()
        day_before = date_from - relativedelta(days=1)
        acc_begin = self._get_accumulated_depreciation(day_before)
        acc_end = self._get_accumulated_depreciation(date_to)
        cost_begin = self.acquisition_value if self._is_owned_at(day_before) else 0.0
        cost_end = self.acquisition_value if self._is_owned_at(date_to) else 0.0
        disposed_in_period = bool(
            self.disposal_date and date_from <= self.disposal_date <= date_to
        )
        return {
            "cost_begin": cost_begin,
            "cost_end": cost_end,
            "book_value_begin": cost_begin - acc_begin if cost_begin else 0.0,
            "book_value_end": cost_end - acc_end if cost_end else 0.0,
            "depreciation_for_period": acc_end - acc_begin,
            "accumulated_depreciation": acc_end,
            "disposal_amount": self.acquisition_value - acc_end
            if disposed_in_period
            else 0.0,
            "disposal_date": self.disposal_date if disposed_in_period else False,
            "depreciation_percentage": float_round(
                1200.0 / self.useful_life_months, precision_digits=2
            ),
        }

    def _get_saft_transactions(self, date_from, date_to):
        """Asset transactions (acquisition, monthly depreciation, write-off)."""
        self.ensure_one()
        transactions = []
        if date_from <= self.acquisition_date <= date_to:
            transactions.append(
                {
                    "id": f"{self.code}-I",
                    "type": "I",
                    "description": self.env._("Acquisition of %s", self.name),
                    "date": self.acquisition_date,
                    "transaction_id": self.acquisition_move_id.name or f"{self.code}-I",
                    "cost": self.acquisition_value,
                    "book_value": self.acquisition_value,
                    "amount": self.acquisition_value,
                    "indicator": "D",
                }
            )
        month = date_from.replace(day=1)
        while month <= date_to:
            month_end = month + relativedelta(months=1, days=-1)
            previous = self._get_accumulated_depreciation(month - relativedelta(days=1))
            current = self._get_accumulated_depreciation(month_end)
            if current - previous > 0:
                transactions.append(
                    {
                        "id": f"{self.code}-NUS-{month:%Y%m}",
                        "type": "NUS",
                        "description": self.env._(
                            "Depreciation %(month)s of %(asset)s",
                            month=f"{month:%Y-%m}",
                            asset=self.name,
                        ),
                        "date": min(month_end, date_to),
                        "transaction_id": f"{self.code}-NUS-{month:%Y%m}",
                        "cost": self.acquisition_value,
                        "book_value": self.acquisition_value - current,
                        "amount": current - previous,
                        "indicator": "K",
                    }
                )
            month += relativedelta(months=1)
        if self.disposal_date and date_from <= self.disposal_date <= date_to:
            accumulated = self._get_accumulated_depreciation(self.disposal_date)
            transactions.append(
                {
                    "id": f"{self.code}-NUR",
                    "type": "NUR",
                    "description": self.env._("Write-off of %s", self.name),
                    "date": self.disposal_date,
                    "transaction_id": self.disposal_move_id.name or f"{self.code}-NUR",
                    "cost": self.acquisition_value,
                    "book_value": 0.0,
                    "amount": self.acquisition_value - accumulated,
                    "indicator": "K",
                }
            )
        return transactions

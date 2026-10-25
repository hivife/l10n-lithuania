from odoo import api, fields, models

JOURNAL_TYPE_MAPPING = {
    "sale": "PARD",
    "purchase": "PIRK",
    "cash": "KASA",
    "bank": "BANK",
    "credit": "BANK",
    "general": "BENDR",
}


class AccountJournal(models.Model):
    _inherit = "account.journal"

    l10n_lt_saft_journal_type = fields.Char(
        string="SAF-T Journal Type",
        size=24,
        compute="_compute_l10n_lt_saft_journal_type",
        store=True,
        readonly=False,
        help="Group of journals this journal belongs to "
        "(GeneralLedgerEntries/Journals/Journal/Type).",
    )

    @api.depends("type")
    def _compute_l10n_lt_saft_journal_type(self):
        for journal in self:
            journal.l10n_lt_saft_journal_type = JOURNAL_TYPE_MAPPING.get(
                journal.type, "KT"
            )

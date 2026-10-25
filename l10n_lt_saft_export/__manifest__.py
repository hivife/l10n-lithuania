# Copyright 2026 Tim Allhoff
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Lithuania SAF-T Export",
    "version": "20.0.1.0.0",
    "category": "Accounting/Localizations/Reporting",
    "summary": "SAF-T LT (XSD 2.01) export for the Lithuanian tax authority (VMI)",
    "author": "Tim Allhoff",
    "maintainers": ["hivife"],
    "website": "https://github.com/hivife/l10n-lithuania",
    "license": "LGPL-3",
    "development_status": "Beta",
    "countries": ["lt"],
    "depends": ["l10n_lt"],
    "external_dependencies": {"python": ["lxml"]},
    "data": [
        "security/ir.access.csv",
        "views/account_account_views.xml",
        "views/account_journal_views.xml",
        "views/res_config_settings_views.xml",
        "views/l10n_lt_saft_asset_views.xml",
        "views/l10n_lt_saft_owner_views.xml",
        "wizard/l10n_lt_saft_export_wizard_views.xml",
        "views/menus.xml",
    ],
    "images": ["static/description/banner.png"],
    "installable": True,
}

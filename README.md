# Lithuanian localization add-ons for Odoo

Odoo 20.0 Community add-ons for Lithuanian legal reporting.

[//]: # (addons)

Available addons
----------------
addon | version | summary
--- | --- | ---
[l10n_lt_saft_export](l10n_lt_saft_export/) | 20.0.1.0.0 | SAF-T LT (XSD 2.01) export for the Lithuanian tax authority (VMI)
[l10n_lt_saft_export_stock](l10n_lt_saft_export_stock/) | 20.0.1.0.0 | Stock movements and physical stock in the Lithuanian SAF-T

[//]: # (end addons)

## Installation

Copy the add-on folders into your Odoo add-ons path (or add this repository to it),
update the apps list and install *Lithuania SAF-T Export*. The stock add-on is installed
automatically when *Inventory* with stock valuation (`stock_account`) is installed.

## Development

```bash
pip install pre-commit && pre-commit install   # code style checks (OCA configuration)
```

Tests run with the standard Odoo test runner, e.g.

```bash
odoo -d test_db -i l10n_lt_saft_export,l10n_lt_saft_export_stock --test-enable \
     --test-tags /l10n_lt_saft_export,/l10n_lt_saft_export_stock --stop-after-init
```

## Licenses

This repository is licensed under [LGPL-3.0](LICENSE). The SAF-T XML schema in
`l10n_lt_saft_export/data/xsd` is published by the Lithuanian State Tax Inspectorate (VMI).

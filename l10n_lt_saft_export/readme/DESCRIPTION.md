**i.SAF (monthly invoice register) is in development and will follow in a separate module.**

Generates the Lithuanian **SAF-T** file (XSD version 2.01) of the State Tax
Inspectorate (VMI) and validates it against the official schema.

- Complete file (`F`) or split by data type (`GL`, `SI`, `PI`, `PA`, `MG`, `AS`)
- Master files, general ledger entries, sales and purchase invoices, payments
- Simple registers for fixed assets and owners
- Stock movements and physical stock with `l10n_lt_saft_export_stock`
- Works with Odoo Community Edition, no Enterprise modules required

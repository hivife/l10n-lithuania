This module generates the Lithuanian standard accounting data file **SAF-T**
(*Standartinė apskaitos duomenų rinkmena*, XSD version **2.01**) requested by the
State Tax Inspectorate (VMI, i.SAF-T) and validates it against the official schema
shipped with the module.

All parts of the file are covered:

| SAF-T part | Source in Odoo |
|---|---|
| Header | company data, SAF-T settings, export wizard |
| GeneralLedgerAccounts | posted journal items; income and expense accounts restart at the beginning of the financial year, the result of previous years is shown on the unaffected earnings account |
| Customers / Suppliers | partners used in the file, receivable / payable balances per account, open invoices at the beginning of the period |
| TaxTable | company taxes; `STITaxCode` is the VMI tax code of the `l10n_lt` taxes (fallback `PVM100`) |
| UOMTable, Products | units of measure and products used in the file |
| AnalysisTypeTable | analytic accounts used in the analytic distribution |
| Assets, AssetTransactions | built-in register of fixed assets (straight-line depreciation) |
| Owners | built-in register of owners / shareholders |
| GeneralLedgerEntries | posted journal entries, grouped by journal |
| SalesInvoices, PurchaseInvoices | customer and vendor invoices and credit notes |
| Payments | entries of bank, cash and credit card journals |
| MovementTypeTable, PhysicalStock, MovementOfGoods | done stock moves, provided by `l10n_lt_saft_export_stock` |

The file can be generated complete (data type `F`) or split by data type
(`GL`, `SI`, `PI`, `PA`, `MG`, `AS`). The data groups written for each data type
follow table 5 of the VMI XSD specification.

The module works with Odoo Community Edition and does not depend on any
Enterprise module.

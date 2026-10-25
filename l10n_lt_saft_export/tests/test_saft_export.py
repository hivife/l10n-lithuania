from datetime import date

from lxml import etree

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_lt_saft_export.models.l10n_lt_saft_export import NS

NSMAP = {"s": NS}


@tagged("post_install_l10n", "post_install", "-at_install")
class TestSaftExport(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("lt")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.write(
            {
                "name": "UAB Testas",
                "vat": "LT123456715",
                "street": "Gedimino pr. 1",
                "city": "Vilnius",
                "zip": "01100",
                "phone": "+37052000000",
                "email": "info@testas.lt",
            }
        )
        cls.company.partner_id._set_additional_identifier("LT_JAK", "304123456")
        cls.company.l10n_lt_saft_contact_id = cls.env["res.partner"].create(
            {
                "name": "Jonas Jonaitis",
                "phone": "+37060000000",
                "email": "jonas@testas.lt",
            }
        )

        cls.customer_lt = cls.env["res.partner"].create(
            {
                "name": "UAB Pirkėjas",
                "is_company": True,
                "vat": "LT100001919017",
                "street": "Laisvės al. 10",
                "city": "Kaunas",
                "zip": "44001",
                "country_id": cls.env.ref("base.lt").id,
            }
        )
        cls.customer_lt._set_additional_identifier("LT_JAK", "123456789")
        cls.supplier_de = cls.env["res.partner"].create(
            {
                "name": "Lieferant GmbH",
                "is_company": True,
                "vat": "DE257486969",
                "street": "Hauptstraße 1",
                "city": "Berlin",
                "zip": "10115",
                "country_id": cls.env.ref("base.de").id,
            }
        )

        # Previous year: an invoice partially paid before the period (open item)
        # and its revenue
        # (previous year result on the unaffected earnings account).
        cls.invoice_2024 = cls.init_invoice(
            "out_invoice",
            partner=cls.customer_lt,
            invoice_date="2024-11-15",
            post=True,
            amounts=[1000.0],
            taxes=cls.tax_sale_a,
        )
        cls._register_payment(cls.invoice_2024, 500.0, "2024-12-10")
        cls._register_payment(
            cls.invoice_2024, cls.invoice_2024.amount_residual, "2025-01-20"
        )

        # Period 2025
        cls.invoice = cls.init_invoice(
            "out_invoice",
            partner=cls.customer_lt,
            invoice_date="2025-03-01",
            post=True,
            products=cls.product_a + cls.product_b,
        )
        cls.refund = (
            cls.env["account.move.reversal"]
            .with_context(
                active_model="account.move",
                active_ids=cls.invoice.ids,
            )
            .create(
                {
                    "date": date(2025, 3, 15),
                    "reason": "Damaged goods",
                    "journal_id": cls.invoice.journal_id.id,
                }
            )
            .refund_moves()
        )
        cls.refund = cls.env["account.move"].browse(cls.refund["res_id"])
        cls.refund.action_post()
        cls.bill = cls.init_invoice(
            "in_invoice",
            partner=cls.supplier_de,
            invoice_date="2025-04-10",
            post=True,
            amounts=[500.0],
            taxes=cls.tax_purchase_a,
        )
        cls._register_payment(cls.bill, cls.bill.amount_total, "2025-05-10")
        cls.misc = cls.env["account.move"].create(
            {
                "move_type": "entry",
                "date": "2025-06-30",
                "ref": "Accrual",
                "line_ids": [
                    Command.create(
                        {
                            "name": "Rent",
                            "account_id": cls.company_data[
                                "default_account_expense"
                            ].id,
                            "debit": 300.0,
                        }
                    ),
                    Command.create(
                        {
                            "name": "Rent",
                            "account_id": cls.company_data[
                                "default_account_payable"
                            ].id,
                            "partner_id": cls.supplier_de.id,
                            "credit": 300.0,
                        }
                    ),
                ],
            }
        )
        cls.misc.action_post()

        asset_account = cls.env["account.account"].search(
            [
                ("company_ids", "in", cls.company.id),
                ("account_type", "=", "asset_fixed"),
            ],
            limit=1,
        )
        cls.asset_old = cls.env["l10n_lt.saft.asset"].create(
            {
                "name": "Delivery van",
                "code": "IT-001",
                "account_id": asset_account.id,
                "partner_id": cls.supplier_de.id,
                "acquisition_date": "2024-06-15",
                "start_date": "2024-06-20",
                "acquisition_value": 12000.0,
                "useful_life_months": 60,
            }
        )
        cls.asset_new = cls.env["l10n_lt.saft.asset"].create(
            {
                "name": "Laptop",
                "code": "IT-002",
                "account_id": asset_account.id,
                "acquisition_date": "2025-03-10",
                "start_date": "2025-03-10",
                "acquisition_value": 6000.0,
                "useful_life_months": 24,
            }
        )
        cls.env["l10n_lt.saft.owner"].create(
            {
                "partner_id": cls.customer_lt.id,
                "owner_code": "123456789",
                "account_id": cls.env["account.account"]
                .search(
                    [
                        ("company_ids", "in", cls.company.id),
                        ("account_type", "=", "equity"),
                    ],
                    limit=1,
                )
                .id,
                "shares_quantity": 100,
                "shares_amount": 2500.0,
                "shares_type": "PP",
            }
        )

    @classmethod
    def _register_payment(cls, move, amount, payment_date):
        cls.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=move.ids,
        ).create({"amount": amount, "payment_date": payment_date})._create_payments()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _generate(
        self, data_type="F", date_from=date(2025, 1, 1), date_to=date(2025, 12, 31)
    ):
        exporter = self.env["l10n_lt.saft.export"]
        options = exporter._saft_get_options(
            self.company, date_from, date_to, data_type=data_type
        )
        content = exporter._saft_generate(options)
        errors = exporter._saft_validate(content)
        self.assertEqual(errors, [], f"{data_type} file is not valid against the XSD")
        return etree.fromstring(content)

    def _values(self, tree, xpath):
        return [node.text or "" for node in tree.xpath(xpath, namespaces=NSMAP)]

    def _sum(self, tree, xpath):
        return round(sum(float(value) for value in self._values(tree, xpath)), 2)

    def _one(self, tree, xpath):
        nodes = tree.xpath(xpath, namespaces=NSMAP)
        self.assertEqual(len(nodes), 1, xpath)
        return nodes[0]

    # -------------------------------------------------------------------------
    # Tests
    # -------------------------------------------------------------------------

    def test_all_data_types_are_valid(self):
        for data_type in ("F", "GL", "SI", "PI", "PA", "MG", "AS"):
            with self.subTest(data_type=data_type):
                tree = self._generate(data_type)
                self.assertEqual(
                    self._values(tree, "//s:Header/s:DataType"), [data_type]
                )

    def test_header(self):
        tree = self._generate()
        header = "/s:AuditFile/s:Header"
        self.assertEqual(self._values(tree, f"{header}/s:AuditFileVersion"), ["2.01"])
        self.assertEqual(
            self._values(tree, f"{header}/s:Company/s:RegistrationNumber"),
            ["304123456"],
        )
        self.assertEqual(
            self._values(
                tree, f"{header}/s:Company/s:TaxRegistration/s:TaxRegistrationNumber"
            ),
            ["LT123456715"],
        )
        self.assertEqual(
            self._values(
                tree, f"{header}/s:Company/s:Contact/s:ContactPerson/s:FirstName"
            ),
            ["Jonas"],
        )
        self.assertEqual(
            self._values(tree, f"{header}/s:Company/s:Contact/s:Telephone"),
            ["+37060000000"],
        )
        self.assertEqual(
            self._values(tree, f"{header}/s:FiscalYearFrom"), ["2025-01-01"]
        )
        self.assertEqual(self._values(tree, f"{header}/s:DefaultCurrencyCode"), ["EUR"])

    def test_general_ledger_is_balanced(self):
        tree = self._generate("GL")
        accounts = "//s:GeneralLedgerAccounts/s:Account"
        self.assertEqual(
            self._sum(tree, f"{accounts}/s:OpeningDebitBalance"),
            self._sum(tree, f"{accounts}/s:OpeningCreditBalance"),
        )
        self.assertEqual(
            self._sum(tree, f"{accounts}/s:ClosingDebitBalance"),
            self._sum(tree, f"{accounts}/s:ClosingCreditBalance"),
        )

        entries = "//s:GeneralLedgerEntries"
        total_debit = float(self._values(tree, f"{entries}/s:TotalDebit")[0])
        self.assertEqual(
            total_debit, float(self._values(tree, f"{entries}/s:TotalCredit")[0])
        )
        self.assertEqual(
            total_debit, self._sum(tree, f"{entries}//s:Line/s:DebitAmount/s:Amount")
        )
        moves = self.env["account.move"].search(
            [
                ("company_id", "=", self.company.id),
                ("state", "=", "posted"),
                ("date", ">=", "2025-01-01"),
                ("date", "<=", "2025-12-31"),
            ]
        )
        self.assertEqual(
            self._values(tree, f"{entries}/s:NumberOfEntries"), [str(len(moves))]
        )
        self.assertEqual(
            len(tree.xpath(f"{entries}//s:Transaction", namespaces=NSMAP)), len(moves)
        )

    def test_previous_year_result_on_unaffected_earnings(self):
        tree = self._generate("GL")
        unaffected = self.company.get_unaffected_earnings_account()
        account = self._one(
            tree,
            f"//s:GeneralLedgerAccounts/s:Account[s:AccountID='{unaffected.code}']",
        )
        self.assertEqual(self._values(account, "s:OpeningCreditBalance"), ["1000.00"])
        income = self.company_data["default_account_revenue"]
        account = self._one(
            tree, f"//s:GeneralLedgerAccounts/s:Account[s:AccountID='{income.code}']"
        )
        self.assertEqual(self._values(account, "s:OpeningDebitBalance"), ["0.00"])

    def test_partner_references_exist_in_master_files(self):
        tree = self._generate()
        customers = set(
            self._values(tree, "//s:MasterFiles/s:Customers/s:Customer/s:CustomerID")
        )
        suppliers = set(
            self._values(tree, "//s:MasterFiles/s:Suppliers/s:Supplier/s:SupplierID")
        )
        for section in ("GeneralLedgerEntries", "SourceDocuments"):
            used_customers = set(self._values(tree, f"//s:{section}//s:CustomerID")) - {
                ""
            }
            used_suppliers = set(self._values(tree, f"//s:{section}//s:SupplierID")) - {
                ""
            }
            self.assertLessEqual(used_customers, customers, section)
            self.assertLessEqual(used_suppliers, suppliers, section)
        self.assertIn(str(self.customer_lt.id), customers)
        self.assertIn(str(self.supplier_de.id), suppliers)
        customer = self._one(
            tree, f"//s:Customers/s:Customer[s:CustomerID='{self.customer_lt.id}']"
        )
        self.assertEqual(self._values(customer, "s:RegistrationNumber"), ["123456789"])
        self.assertEqual(
            self._values(customer, "s:TaxRegistration/s:TaxRegistrationNumber"),
            ["LT100001919017"],
        )

    def test_open_sales_invoices_at_period_start(self):
        tree = self._generate()
        open_invoice = self._one(
            tree,
            f"//s:Customer[s:CustomerID='{self.customer_lt.id}']/s:OpenSalesInvoices/s:OpenSalesInvoice",
        )
        self.assertEqual(
            self._values(open_invoice, "s:InvoiceNo"), [self.invoice_2024.name]
        )
        self.assertEqual(
            self._values(open_invoice, "s:Amount"),
            [f"{self.invoice_2024.amount_total:.2f}"],
        )
        self.assertEqual(
            self._values(open_invoice, "s:UnpaidAmount"),
            [f"{self.invoice_2024.amount_total - 500.0:.2f}"],
        )
        self.assertEqual(self._values(open_invoice, "s:DebitCreditIndicator"), ["D"])

    def test_sales_invoices(self):
        tree = self._generate("SI")
        self.assertEqual(
            self._values(tree, "//s:SalesInvoices/s:NumberOfEntries"), ["2"]
        )
        invoice = self._one(
            tree, f"//s:SalesInvoices/s:Invoice[s:InvoiceNo='{self.invoice.name}']"
        )
        self.assertEqual(self._values(invoice, "s:InvoiceType"), ["SF"])
        self.assertEqual(
            self._values(invoice, "s:CustomerInfo/s:CustomerID"),
            [str(self.customer_lt.id)],
        )
        self.assertEqual(
            self._values(invoice, "s:DocumentTotals/s:NetTotal"),
            [f"{self.invoice.amount_untaxed:.2f}"],
        )
        self.assertEqual(
            self._values(invoice, "s:DocumentTotals/s:GrossTotal"),
            [f"{self.invoice.amount_total:.2f}"],
        )
        self.assertEqual(
            self._values(invoice, "s:Line/s:DebitCreditIndicator"), ["K", "K"]
        )
        self.assertEqual(
            self._sum(
                invoice, "s:DocumentTotals/s:TaxInformationTotals/s:TaxAmount/s:Amount"
            ),
            round(self.invoice.amount_tax, 2),
        )
        refund = self._one(
            tree, f"//s:SalesInvoices/s:Invoice[s:InvoiceNo='{self.refund.name}']"
        )
        self.assertEqual(self._values(refund, "s:InvoiceType"), ["KS"])
        self.assertEqual(
            self._values(refund, "s:References/s:CreditNote/s:Reference"),
            [self.invoice.name],
        )
        self.assertEqual(
            self._values(refund, "s:Line/s:DebitCreditIndicator"), ["D", "D"]
        )
        # invoice and refund cancel each other out
        self.assertEqual(
            self._values(tree, "//s:SalesInvoices/s:TotalDebit"),
            self._values(tree, "//s:SalesInvoices/s:TotalCredit"),
        )
        # SI files carry neither the ledger nor the GL accounts
        self.assertFalse(tree.xpath("//s:GeneralLedgerEntries", namespaces=NSMAP))
        self.assertFalse(tree.xpath("//s:GeneralLedgerAccounts", namespaces=NSMAP))
        products = set(
            self._values(tree, "//s:MasterFiles/s:Products/s:Product/s:ProductCode")
        )
        self.assertLessEqual(
            set(self._values(tree, "//s:SalesInvoices//s:Line/s:ProductCode")), products
        )

    def test_purchase_invoices_and_payments(self):
        tree = self._generate("F")
        bill = self._one(
            tree, f"//s:PurchaseInvoices/s:Invoice[s:InvoiceNo='{self.bill.name}']"
        )
        self.assertEqual(
            self._values(bill, "s:SupplierInfo/s:SupplierID"),
            [str(self.supplier_de.id)],
        )
        self.assertEqual(self._values(bill, "s:SupplierInfo/s:Country"), ["DE"])
        self.assertEqual(self._values(bill, "s:Line/s:DebitCreditIndicator"), ["D"])

        payments = "//s:SourceDocuments/s:Payments"
        self.assertEqual(
            self._values(tree, f"{payments}/s:TotalDebit"),
            self._values(tree, f"{payments}/s:TotalCredit"),
        )
        supplier_payment = tree.xpath(
            f"{payments}/s:Payment[s:Lines/s:Line/s:SupplierID='{self.supplier_de.id}']",
            namespaces=NSMAP,
        )
        self.assertEqual(len(supplier_payment), 1)
        self.assertEqual(
            self._values(supplier_payment[0], "s:GrossTotal"),
            [f"{self.bill.amount_total:.2f}"],
        )
        self.assertIn(
            self.bill.name,
            self._values(supplier_payment[0], "s:Lines/s:Line/s:SourceDocumentID"),
        )

    def test_assets(self):
        tree = self._generate("AS")
        old = self._one(
            tree, "//s:Assets/s:Asset[s:AssetID='IT-001']/s:Valuations/s:Valuation"
        )
        self.assertEqual(
            self._values(old, "s:AcquisitionAndProductionCostsBegin"), ["12000.00"]
        )
        self.assertEqual(
            self._values(old, "s:BookValueBegin"), ["10800.00"]
        )  # 6 months in 2024
        self.assertEqual(self._values(old, "s:DepreciationForPeriod"), ["2400.00"])
        self.assertEqual(self._values(old, "s:AccumulatedDepreciation"), ["3600.00"])
        self.assertEqual(self._values(old, "s:BookValueEnd"), ["8400.00"])
        new = self._one(
            tree, "//s:Assets/s:Asset[s:AssetID='IT-002']/s:Valuations/s:Valuation"
        )
        self.assertEqual(
            self._values(new, "s:AcquisitionAndProductionCostsBegin"), ["0.00"]
        )
        self.assertEqual(
            self._values(new, "s:DepreciationForPeriod"), ["2250.00"]
        )  # April - December
        types = self._values(
            tree, "//s:AssetTransaction[s:AssetID='IT-002']/s:AssetTransactionType"
        )
        self.assertEqual(types, ["I"] + ["NUS"] * 9)
        self.assertEqual(
            self._values(tree, "//s:AssetTransactions/s:NumberOfAssetTransactions"),
            [str(12 + 10)],
        )
        # the supplier of an asset is part of the suppliers master file
        self.assertIn(
            str(self.supplier_de.id),
            self._values(tree, "//s:Suppliers/s:Supplier/s:SupplierID"),
        )

    def test_asset_disposal(self):
        self.asset_old.disposal_date = date(2025, 8, 31)
        tree = self._generate("AS")
        valuation = self._one(
            tree, "//s:Assets/s:Asset[s:AssetID='IT-001']/s:Valuations/s:Valuation"
        )
        # depreciated until August: 6 + 8 months
        self.assertEqual(
            self._values(valuation, "s:AccumulatedDepreciation"), ["2800.00"]
        )
        self.assertEqual(self._values(valuation, "s:AssetDisposal"), ["9200.00"])
        self.assertEqual(
            self._values(valuation, "s:AcquisitionAndProductionCostsEnd"), ["0.00"]
        )
        self.assertEqual(self._values(valuation, "s:BookValueEnd"), ["0.00"])
        types = self._values(
            tree, "//s:AssetTransaction[s:AssetID='IT-001']/s:AssetTransactionType"
        )
        self.assertEqual(types, ["NUS"] * 8 + ["NUR"])

    def test_owners_and_tax_table(self):
        tree = self._generate("F")
        self.assertEqual(
            self._values(tree, "//s:Owners/s:Owner/s:OwnerID"), ["123456789"]
        )
        self.assertEqual(self._values(tree, "//s:Owners/s:Owner/s:SharesType"), ["PP"])
        tax_codes = set(self._values(tree, "//s:TaxTable//s:TaxCodeDetail/s:TaxCode"))
        used_codes = set(self._values(tree, "//s:SourceDocuments//s:TaxCode")) | set(
            self._values(tree, "//s:GeneralLedgerEntries//s:TaxCode")
        )
        self.assertLessEqual(used_codes, tax_codes)
        detail = self._one(
            tree, f"//s:TaxTable//s:TaxCodeDetail[s:TaxCode='{self.tax_sale_a.id}']"
        )
        self.assertEqual(
            self._values(detail, "s:STITaxCode"),
            [self.tax_sale_a.l10n_lt_tax_code or "PVM100"],
        )

    def test_period_longer_than_fiscal_year_is_refused(self):
        with self.assertRaises(UserError):
            self.env["l10n_lt.saft.export"]._saft_get_options(
                self.company, date(2024, 6, 1), date(2025, 5, 31)
            )

    def test_missing_company_data_is_reported(self):
        self.company.l10n_lt_saft_contact_id.phone = False
        self.company.phone = False
        with self.assertRaisesRegex(UserError, "phone"):
            self._generate()

    def test_company_without_jak_uses_vat(self):
        self.company.partner_id._set_additional_identifier("LT_JAK", False)
        tree = self._generate()
        self.assertEqual(
            self._values(tree, "//s:Header/s:Company/s:RegistrationNumber"),
            ["LT123456715"],
        )

    def test_monthly_file_part(self):
        tree = self._generate("F", date(2025, 3, 1), date(2025, 3, 31))
        self.assertEqual(
            self._values(tree, "//s:Header/s:SelectionCriteria/s:PeriodStart"),
            ["2025-03-01"],
        )
        self.assertEqual(
            self._values(tree, "//s:Transaction/s:Period"),
            ["3"] * len(tree.xpath("//s:Transaction", namespaces=NSMAP)),
        )
        self.assertEqual(
            self._values(tree, "//s:SalesInvoices/s:NumberOfEntries"), ["2"]
        )

    def test_wizard(self):
        wizard = self.env["l10n_lt.saft.export.wizard"].create(
            {
                "date_from": "2025-01-01",
                "date_to": "2025-12-31",
                "data_type": "F",
            }
        )
        wizard.action_generate()
        self.assertEqual(wizard.state, "done")
        self.assertEqual(wizard.file_name, "SAF-T_304123456_20250101_20251231_F.xml")
        tree = etree.fromstring(wizard.file_data.content)
        self.assertEqual(tree.tag, f"{{{NS}}}AuditFile")

import re
from collections import defaultdict

from lxml import etree

from odoo import api, fields, models, release
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_repr, float_round
from odoo.tools.misc import file_path

NS = "https://www.vmi.lt/cms/saf-t"
XSD_PATH = "l10n_lt_saft_export/data/xsd/SAF-T_v2.01_20190306.xsd"
AUDIT_FILE_VERSION = "2.01"

DATA_TYPES = [
    ("F", "F - Complete file"),
    ("GL", "GL - General ledger"),
    ("SI", "SI - Sales invoices"),
    ("PI", "PI - Purchase invoices"),
    ("PA", "PA - Payments"),
    ("MG", "MG - Movement of goods"),
    ("AS", "AS - Asset transactions"),
]

# Which data groups may be filled for which DataType
# (SAF-T XSD specification v2.01, chapter 2.2, table 5).
SECTIONS_BY_DATA_TYPE = {
    "GeneralLedgerAccounts": {"F", "GL"},
    "Customers": {"F", "GL", "MG", "SI", "PI", "PA"},
    "Suppliers": {"F", "GL", "MG", "SI", "PI", "PA", "AS"},
    "TaxTable": {"F", "GL", "MG", "SI", "PI"},
    "UOMTable": {"F", "MG", "SI", "PI"},
    "AnalysisTypeTable": {"F", "GL", "SI", "PI", "PA"},
    "MovementTypeTable": {"F", "MG"},
    "Products": {"F", "MG", "SI", "PI"},
    "PhysicalStock": {"F", "MG", "SI", "PI"},
    "Assets": {"F", "AS"},
    "Owners": {"F", "GL"},
    "GeneralLedgerEntries": {"F", "GL"},
    "SalesInvoices": {"F", "SI"},
    "PurchaseInvoices": {"F", "PI"},
    "Payments": {"F", "PA"},
    "MovementOfGoods": {"F", "MG"},
    "AssetTransactions": {"F", "AS"},
}

VAT_TAX_TYPE = "PVM"
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_SCHEMA = None


# -----------------------------------------------------------------------------
# XML helpers
# -----------------------------------------------------------------------------


def _qname(tag):
    return f"{{{NS}}}{tag}"


def new_node(tag):
    """Detached element (namespaces are cleaned up when the file is serialized)."""
    return etree.Element(_qname(tag), nsmap={None: NS})


def add(parent, tag, text=None):
    node = etree.SubElement(parent, _qname(tag))
    if text is not None:
        node.text = text
    return node


def add_opt(parent, tag, text):
    """Add the element only if there is a value."""
    if text in (None, False, ""):
        return None
    return add(parent, tag, text)


def txt(value, size=None):
    if not value:
        return ""
    value = _INVALID_XML_CHARS.sub("", str(value))
    value = " ".join(value.split())
    return value[:size] if size else value


def amount(value):
    """SAFmonetaryType: non-negative, 2 decimals."""
    return float_repr(abs(float_round(value or 0.0, precision_digits=2)), 2)


def signed_amount(value):
    """SAFmonetaryType2: signed, 2 decimals."""
    return float_repr(float_round(value or 0.0, precision_digits=2) + 0.0, 2)


def decimal(value, digits=2):
    text = float_repr(float_round(value or 0.0, precision_digits=digits) + 0.0, digits)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def quantity(value):
    """SAFquantityType: non-negative, max 10 decimals."""
    return decimal(abs(value or 0.0), 10)


def date_str(value):
    return fields.Date.to_string(value)


def datetime_str(value):
    return fields.Datetime.to_datetime(value).replace(microsecond=0).isoformat()


class L10nLtSaftExport(models.AbstractModel):
    """Generator of the Lithuanian SAF-T file (XSD 2.01).

    The file is built section by section. Every ``_saft_add_*`` method can be
    extended by other
    modules (e.g. ``l10n_lt_saft_export_stock`` for the stock related sections).
    """

    _name = "l10n_lt.saft.export"
    _description = "Lithuanian SAF-T generator"

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @api.model
    def _saft_get_options(
        self,
        company,
        date_from,
        date_to,
        data_type="F",
        tax_accounting_basis=None,
        entity=None,
    ):
        fiscal_year = company.compute_fiscalyear_dates(date_from)
        if date_from > date_to:
            raise UserError(self.env._("The start date must be before the end date."))
        if date_to > fiscal_year["date_to"]:
            raise UserError(
                self.env._(
                    "A SAF-T file (or file part) may not cover more than one "
                    "financial year. "
                    "The financial year of %(date_from)s ends on %(fy_end)s.",
                    date_from=date_from,
                    fy_end=fiscal_year["date_to"],
                )
            )
        entity = re.sub(
            r"[^A-Z0-9_]",
            "_",
            (entity or company.l10n_lt_saft_entity or "ODOO").upper(),
        )[:20]
        return {
            "company": company,
            "date_from": date_from,
            "date_to": date_to,
            "data_type": data_type,
            "tax_accounting_basis": tax_accounting_basis
            or company.l10n_lt_saft_tax_accounting_basis
            or "K",
            "entity": entity or "ODOO",
            "fiscal_year_from": fiscal_year["date_from"],
            "fiscal_year_to": fiscal_year["date_to"],
            "currency": company.currency_id,
        }

    @api.model
    def _saft_generate(self, options):
        """Return the SAF-T file as bytes."""
        return self.with_company(options["company"])._saft_generate_xml(options)

    @api.model
    def _saft_generate_xml(self, options):
        self._saft_check_company(options)
        ctx = {
            "customers": self.env["res.partner"],
            "suppliers": self.env["res.partner"],
            "products": self.env["product.product"],
            "uoms": self.env["uom.uom"],
            "taxes": self.env["account.tax"],
            "analytic_accounts": self.env["account.analytic.account"],
            "accounts": self.env["account.account"],
        }

        root = new_node("AuditFile")
        self._saft_add_header(options, root)

        # Source documents and ledger entries are built first: they collect the
        # partners,
        # products, taxes, ... the master files must contain.
        gl_entries = (
            self._saft_build_general_ledger_entries(options, ctx)
            if self._saft_has(options, "GeneralLedgerEntries")
            else None
        )
        source_documents = self._saft_build_source_documents(options, ctx)
        master_files = self._saft_build_master_files(options, ctx)

        for node in (master_files, gl_entries, source_documents):
            if node is not None and len(node):
                root.append(node)
        etree.cleanup_namespaces(root, top_nsmap={None: NS})
        return etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", pretty_print=True
        )

    @api.model
    def _saft_validate(self, content):
        """Validate against the official XSD, return a list of error messages."""
        global _SCHEMA
        if _SCHEMA is None:
            with open(file_path(XSD_PATH), "rb") as xsd_file:
                _SCHEMA = etree.XMLSchema(etree.parse(xsd_file))
        document = etree.fromstring(content)
        if _SCHEMA.validate(document):
            return []
        return [f"line {error.line}: {error.message}" for error in _SCHEMA.error_log]

    @api.model
    def _saft_get_filename(self, options):
        company = options["company"]
        code = (
            company.partner_id._get_additional_identifier("LT_JAK")
            or company.vat
            or str(company.id)
        )
        return "SAF-T_{code}_{df}_{dt}_{dtype}.xml".format(
            code=re.sub(r"[^A-Za-z0-9]", "", code),
            df=options["date_from"].strftime("%Y%m%d"),
            dt=options["date_to"].strftime("%Y%m%d"),
            dtype=options["data_type"],
        )

    # -------------------------------------------------------------------------
    # Generic helpers
    # -------------------------------------------------------------------------

    @api.model
    def _saft_has(self, options, section):
        return options["data_type"] in SECTIONS_BY_DATA_TYPE[section]

    @api.model
    def _saft_check_company(self, options):
        company = options["company"]
        errors = []
        if not (self._saft_registration_number(company.partner_id) or company.vat):
            errors.append(
                self.env._("Set the Company ID (JAK) or the Tax ID of the company.")
            )
        if not company.city or not company.zip:
            errors.append(
                self.env._("Set the city and the postal code of the company.")
            )
        contact = company.l10n_lt_saft_contact_id
        if not (contact.phone or company.phone):
            errors.append(
                self.env._(
                    "Set a phone number on the company or on the SAF-T contact person."
                )
            )
        if errors:
            raise UserError(
                self.env._(
                    "The SAF-T file cannot be generated:\n%s",
                    "\n".join(f"- {e}" for e in errors),
                )
            )

    @api.model
    def _saft_base_domain(self, options):
        return [
            ("company_id", "=", options["company"].id),
            ("parent_state", "=", "posted"),
        ]

    @api.model
    def _saft_partner_code(self, partner):
        return str(partner.commercial_partner_id.id) if partner else ""

    @api.model
    def _saft_registration_number(self, partner):
        partner = partner.commercial_partner_id
        if not partner.is_company and partner.parent_id:
            partner = partner.parent_id
        return (
            partner._get_additional_identifier("LT_JAK")
            if partner.country_code == "LT"
            else ""
        )

    @api.model
    def _saft_account_code(self, account):
        return txt(account.code, 70)

    @api.model
    def _saft_product_code(self, product):
        return txt(product.default_code or str(product.id), 70)

    @api.model
    def _saft_uom_code(self, uom):
        return txt(uom.name, 24)

    @api.model
    def _saft_tax_code(self, tax):
        return str(tax.id)

    @api.model
    def _saft_period(self, options, date):
        """Month of the financial year (1-12) and the year it ends in."""
        fy_from = options["fiscal_year_from"]
        period = (date.year - fy_from.year) * 12 + date.month - fy_from.month + 1
        return period, options["fiscal_year_to"].year

    @api.model
    def _saft_is_supplier_line(self, line):
        if line.account_id.account_type == "liability_payable":
            return True
        if line.account_id.account_type == "asset_receivable":
            return False
        if line.move_id.is_purchase_document(include_receipts=True):
            return True
        if line.move_id.is_sale_document(include_receipts=True):
            return False
        partner = line.partner_id.commercial_partner_id
        return bool(partner.supplier_rank and not partner.customer_rank)

    @api.model
    def _saft_add_partner_reference(self, parent, ctx, partner, is_supplier):
        """Add the choice CustomerID | SupplierID and register the partner."""
        partner = partner.commercial_partner_id
        if is_supplier:
            add(parent, "SupplierID", self._saft_partner_code(partner))
            ctx["suppliers"] |= partner
        else:
            add(parent, "CustomerID", self._saft_partner_code(partner))
            ctx["customers"] |= partner

    @api.model
    def _saft_add_amount_structure(self, parent, tag, value, currency, currency_value):
        node = add(parent, tag)
        add(node, "Amount", amount(value))
        add(node, "CurrencyCode", currency.name)
        add(node, "CurrencyAmount", amount(currency_value))
        return node

    @api.model
    def _saft_add_address(self, parent, tag, partner, address_type="BA"):
        node = add(parent, tag)
        add_opt(node, "StreetName", txt(partner.street, 70))
        add_opt(node, "Number", txt(partner.street2, 18))
        add(node, "City", txt(partner.city, 35))
        add(node, "PostalCode", txt(partner.zip, 18))
        add(node, "Country", partner.country_id.code or "")
        add(node, "AddressType", address_type)
        return node

    @api.model
    def _saft_add_tax_registration(self, parent, partner, tag="TaxRegistration"):
        partner = partner.commercial_partner_id
        if not partner.vat or partner.vat == "/":
            return
        node = add(parent, tag)
        add(node, "TaxRegistrationNumber", txt(partner.vat, 35))
        add(node, "TaxType", VAT_TAX_TYPE)
        add_opt(node, "Country", partner.country_id.code)

    @api.model
    def _saft_add_bank_accounts(self, parent, banks):
        for bank in banks:
            number = (bank.account_number or "").replace(" ", "")
            if not number:
                continue
            node = add(parent, "BankAccount")
            add(
                node,
                "IBANNumber" if bank.account_type == "iban" else "BankAccountNumber",
                txt(number, 35),
            )

    @api.model
    def _saft_add_analysis(self, parent, ctx, line, tag="Analysis"):
        for key, percentage in (line.analytic_distribution or {}).items():
            for analytic_id in str(key).split(","):
                account = (
                    self.env["account.analytic.account"]
                    .browse(int(analytic_id))
                    .exists()
                )
                if not account:
                    continue
                ctx["analytic_accounts"] |= account
                node = add(parent, tag)
                add(node, "AnalysisType", txt(account.plan_id.name, 24))
                add(node, "AnalysisID", txt(account.code or str(account.id), 256))
                self._saft_add_amount_structure(
                    node,
                    "AnalysisAmount",
                    line.balance * percentage / 100.0,
                    line.currency_id,
                    line.amount_currency * percentage / 100.0,
                )

    @api.model
    def _saft_add_tax_information(
        self, parent, tag, tax, base, tax_amount, currency, tax_amount_currency
    ):
        node = add(parent, tag)
        add(node, "TaxType", VAT_TAX_TYPE)
        add(node, "TaxCode", self._saft_tax_code(tax))
        if tax.amount_type in ("percent", "division"):
            add(node, "TaxPercentage", decimal(tax.amount))
        add(node, "TaxBase", decimal(abs(base)))
        self._saft_add_amount_structure(
            node, "TaxAmount", tax_amount, currency, tax_amount_currency
        )
        return node

    # -------------------------------------------------------------------------
    # 1. Header
    # -------------------------------------------------------------------------

    @api.model
    def _saft_add_header(self, options, root):
        company = options["company"]
        header = add(root, "Header")
        add(header, "AuditFileVersion", AUDIT_FILE_VERSION)
        add(header, "AuditFileCountry", "LT")
        add(header, "AuditFileDateCreated", datetime_str(fields.Datetime.now()))
        add(header, "SoftwareCompanyName", "Odoo S.A.")
        add(header, "SoftwareID", "Odoo Community Edition")
        add(header, "SoftwareVersion", txt(release.version, 18))

        company_node = add(header, "Company")
        add(
            company_node,
            "RegistrationNumber",
            txt(self._saft_registration_number(company.partner_id) or company.vat, 35),
        )
        add(company_node, "Name", txt(company.name, 70))
        self._saft_add_address(company_node, "Address", company.partner_id)
        contact = company.l10n_lt_saft_contact_id
        contact_node = add(company_node, "Contact")
        person = add(contact_node, "ContactPerson")
        first_name, last_name = self._saft_split_name(contact.name or company.name)
        add(person, "FirstName", txt(first_name, 35))
        add(person, "LastName", txt(last_name, 70))
        add(contact_node, "Telephone", txt(contact.phone or company.phone, 18))
        add_opt(contact_node, "Email", txt(contact.email or company.email, 70))
        self._saft_add_tax_registration(company_node, company.partner_id)
        self._saft_add_bank_accounts(company_node, company.partner_id.bank_ids)

        add(header, "DefaultCurrencyCode", options["currency"].name)
        criteria = add(header, "SelectionCriteria")
        add(criteria, "SelectionStartDate", date_str(options["date_from"]))
        add(criteria, "SelectionEndDate", date_str(options["date_to"]))
        add(criteria, "PeriodStart", date_str(options["date_from"]))
        add(criteria, "PeriodEnd", date_str(options["date_to"]))
        add(header, "TaxAccountingBasis", options["tax_accounting_basis"])
        add(header, "FiscalYearFrom", date_str(options["fiscal_year_from"]))
        add(header, "FiscalYearTo", date_str(options["fiscal_year_to"]))
        add(header, "Entity", options["entity"])
        add(header, "DataType", options["data_type"])
        add(header, "NumberOfParts", "1")
        add(header, "PartNumber", "1")
        return header

    @api.model
    def _saft_split_name(self, name):
        parts = (name or "").strip().split(" ", 1)
        if len(parts) == 1:
            return parts[0], parts[0]
        return parts[0], parts[1]

    # -------------------------------------------------------------------------
    # 2. Master files
    # -------------------------------------------------------------------------

    @api.model
    def _saft_build_master_files(self, options, ctx):
        master = new_node("MasterFiles")
        # Asset suppliers and owners must be known before the suppliers are written.
        assets = (
            self._saft_get_assets(options)
            if self._saft_has(options, "Assets")
            else self.env["l10n_lt.saft.asset"]
        )
        ctx["suppliers"] |= assets.partner_id.commercial_partner_id

        if self._saft_has(options, "GeneralLedgerAccounts"):
            self._saft_add_general_ledger_accounts(options, ctx, master, assets)
        if self._saft_has(options, "Customers"):
            self._saft_add_partners(options, ctx, master, is_supplier=False)
        if self._saft_has(options, "Suppliers"):
            self._saft_add_partners(options, ctx, master, is_supplier=True)
        if self._saft_has(options, "TaxTable"):
            self._saft_add_tax_table(options, ctx, master)
        if self._saft_has(options, "Products"):
            # products register their units of measure, so they are collected
            # before the UOM table
            products_node = self._saft_build_products(options, ctx)
        else:
            products_node = None
        if self._saft_has(options, "UOMTable"):
            self._saft_add_uom_table(options, ctx, master)
        if self._saft_has(options, "AnalysisTypeTable"):
            self._saft_add_analysis_type_table(options, ctx, master)
        if self._saft_has(options, "MovementTypeTable"):
            self._saft_add_movement_type_table(options, ctx, master)
        if products_node is not None and len(products_node):
            master.append(products_node)
        if self._saft_has(options, "PhysicalStock"):
            self._saft_add_physical_stock(options, ctx, master)
        if assets:
            self._saft_add_assets(options, ctx, master, assets)
        if self._saft_has(options, "Owners"):
            self._saft_add_owners(options, ctx, master)
        return master

    @api.model
    def _saft_get_account_balances(self, options, date_to, strict):
        """Balance per account at ``date_to`` (exclusive when ``strict``).

        Income and expense accounts start from zero at the beginning of the
        financial year;
        the result of the previous years is shown on the unaffected earnings account.
        """
        company = options["company"]
        operator = "<" if strict else "<="
        domain = self._saft_base_domain(options) + [("date", operator, date_to)]
        balances = defaultdict(float)
        for account, balance in self.env["account.move.line"]._read_group(
            domain + [("account_id.include_initial_balance", "=", True)],
            ["account_id"],
            ["balance:sum"],
        ):
            balances[account] += balance
        for account, balance in self.env["account.move.line"]._read_group(
            domain
            + [
                ("account_id.include_initial_balance", "=", False),
                ("date", ">=", options["fiscal_year_from"]),
            ],
            ["account_id"],
            ["balance:sum"],
        ):
            balances[account] += balance
        previous_result = self.env["account.move.line"]._read_group(
            domain
            + [
                ("account_id.include_initial_balance", "=", False),
                ("date", "<", options["fiscal_year_from"]),
            ],
            [],
            ["balance:sum"],
        )[0][0]
        if previous_result:
            balances[company.get_unaffected_earnings_account()] += previous_result
        return balances

    @api.model
    def _saft_add_general_ledger_accounts(self, options, ctx, master, assets):
        opening = self._saft_get_account_balances(
            options, options["date_from"], strict=True
        )
        closing = self._saft_get_account_balances(
            options, options["date_to"], strict=False
        )
        used_accounts = self.env["account.move.line"]._read_group(
            self._saft_base_domain(options) + [("date", "<=", options["date_to"])],
            ["account_id"],
        )
        accounts = self.env["account.account"].union(
            account for (account,) in used_accounts
        )
        accounts |= self.env["account.account"].union(list(opening) + list(closing))
        accounts |= ctx["accounts"] | assets.account_id | assets.depreciation_account_id
        accounts |= options["company"].l10n_lt_saft_owner_ids.account_id
        if not accounts:
            return
        node = add(master, "GeneralLedgerAccounts")
        for account in accounts.sorted(lambda a: a.code or ""):
            account_node = add(node, "Account")
            add(account_node, "AccountID", self._saft_account_code(account))
            add(account_node, "AccountDescription", txt(account.name, 256))
            add(
                account_node,
                "AccountTableID",
                txt(account.l10n_lt_saft_account_table_id or account.code, 70),
            )
            add(
                account_node,
                "AccountTableDescription",
                txt(
                    account.l10n_lt_saft_account_table_description or account.name, 256
                ),
            )
            add(account_node, "AccountType", account.l10n_lt_saft_account_type or "KT")
            for prefix, balance in (
                ("Opening", opening.get(account, 0.0)),
                ("Closing", closing.get(account, 0.0)),
            ):
                side = "Credit" if float_round(balance, 2) < 0 else "Debit"
                add(account_node, f"{prefix}{side}Balance", amount(balance))

    @api.model
    def _saft_get_partner_balances(self, options, account_type, date_to, strict):
        domain = self._saft_base_domain(options) + [
            ("account_id.account_type", "=", account_type),
            ("partner_id", "!=", False),
            ("date", "<" if strict else "<=", date_to),
        ]
        balances = defaultdict(float)
        for partner, account, balance in self.env["account.move.line"]._read_group(
            domain, ["partner_id", "account_id"], ["balance:sum"]
        ):
            balances[partner.commercial_partner_id, account] += balance
        return balances

    @api.model
    def _saft_add_partners(self, options, ctx, master, is_supplier):
        account_type = "liability_payable" if is_supplier else "asset_receivable"
        opening = self._saft_get_partner_balances(
            options, account_type, options["date_from"], strict=True
        )
        closing = self._saft_get_partner_balances(
            options, account_type, options["date_to"], strict=False
        )
        partners = ctx["suppliers" if is_supplier else "customers"]
        if options["data_type"] in ("F", "GL"):
            partners |= self.env["res.partner"].union(
                partner for partner, _account in closing
            )
        if not partners:
            return
        accounts_by_partner = defaultdict(lambda: self.env["account.account"])
        for partner, account in list(opening) + list(closing):
            accounts_by_partner[partner] |= account
        open_invoices = self._saft_get_open_invoices(options, account_type)

        tag = "Supplier" if is_supplier else "Customer"
        node = add(master, "Suppliers" if is_supplier else "Customers")
        for partner in partners.sorted("id"):
            partner_node = add(node, tag)
            add_opt(
                partner_node,
                "RegistrationNumber",
                txt(self._saft_registration_number(partner), 35),
            )
            add(partner_node, "Name", txt(partner.name, 70))
            self._saft_add_address(partner_node, "Address", partner)
            self._saft_add_tax_registration(partner_node, partner)
            self._saft_add_bank_accounts(partner_node, partner.bank_ids)
            add(partner_node, f"{tag}ID", self._saft_partner_code(partner))
            if is_supplier:
                add(partner_node, "SelfBillingIndicator", "")
            accounts = accounts_by_partner[partner]
            if not accounts:
                accounts = (
                    partner.with_company(options["company"]).property_account_payable_id
                    if is_supplier
                    else partner.with_company(
                        options["company"]
                    ).property_account_receivable_id
                )
            for account in accounts.sorted("id"):
                account_node = add(partner_node, "Accounts")
                add(account_node, "AccountID", self._saft_account_code(account))
                ctx["accounts"] |= account
                for prefix, balance in (
                    ("Opening", opening.get((partner, account), 0.0)),
                    ("Closing", closing.get((partner, account), 0.0)),
                ):
                    side = "Credit" if float_round(balance, 2) < 0 else "Debit"
                    add(account_node, f"{prefix}{side}Balance", amount(balance))
            if open_invoices.get(partner):
                open_node = add(
                    partner_node,
                    "OpenPurchaseInvoices" if is_supplier else "OpenSalesInvoices",
                )
                for values in open_invoices[partner]:
                    self._saft_add_open_invoice(
                        open_node,
                        "OpenPurchaseInvoice" if is_supplier else "OpenSalesInvoice",
                        values,
                    )

    @api.model
    def _saft_get_open_invoices(self, options, account_type):
        """Invoices not (fully) paid at the beginning of the period, per partner."""
        date_from = options["date_from"]
        lines = self.env["account.move.line"].search(
            self._saft_base_domain(options)
            + [
                ("account_id.account_type", "=", account_type),
                (
                    "move_id.move_type",
                    "in",
                    ("out_invoice", "out_refund", "in_invoice", "in_refund"),
                ),
                ("date", "<", date_from),
                "|",
                "|",
                ("reconciled", "=", False),
                ("matched_debit_ids.max_date", ">=", date_from),
                ("matched_credit_ids.max_date", ">=", date_from),
            ]
        )
        result = defaultdict(list)
        currency = options["currency"]
        for line in lines:
            partials = line.matched_debit_ids | line.matched_credit_ids
            paid = sum(
                partials.filtered(lambda p: p.max_date < date_from).mapped("amount")
            )
            unpaid = abs(line.balance) - paid
            if float_is_zero(unpaid, precision_rounding=currency.rounding):
                continue
            ratio = unpaid / abs(line.balance) if line.balance else 0.0
            move = line.move_id
            result[line.partner_id.commercial_partner_id].append(
                {
                    "number": move.name,
                    "invoice_date": move.invoice_date or move.date,
                    "date": move.date,
                    "amount": line.balance,
                    "amount_currency": line.amount_currency,
                    "unpaid": unpaid,
                    "unpaid_currency": line.amount_currency * ratio,
                    "currency": line.currency_id,
                    "indicator": "D" if line.balance > 0 else "K",
                }
            )
        return result

    @api.model
    def _saft_add_open_invoice(self, parent, tag, values):
        node = add(parent, tag)
        add(node, "InvoiceNo", txt(values["number"], 70))
        add(node, "InvoiceDate", date_str(values["invoice_date"]))
        add(node, "GLPostingDate", date_str(values["date"]))
        add(node, "TransactionID", txt(values["number"], 70))
        add(node, "Amount", amount(values["amount"]))
        add(node, "CurrencyAmount", amount(values["amount_currency"]))
        add(node, "CurrencyCode1", values["currency"].name)
        add(node, "UnpaidAmount", amount(values["unpaid"]))
        add(node, "CurrencyUnpaidAmount", amount(values["unpaid_currency"]))
        add(node, "CurrencyCode", values["currency"].name)
        add(node, "DebitCreditIndicator", values["indicator"])

    @api.model
    def _saft_add_tax_table(self, options, ctx, master):
        taxes = ctx["taxes"] | self.env["account.tax"].with_context(
            active_test=False
        ).search(
            [
                ("company_id", "=", options["company"].id),
                "|",
                ("active", "=", True),
                ("id", "in", ctx["taxes"].ids),
            ]
        )
        taxes = taxes.flatten_taxes_hierarchy().filtered(
            lambda t: t.amount_type in ("percent", "division", "fixed")
        )
        if not taxes:
            return
        table = add(master, "TaxTable")
        entry = add(table, "TaxTableEntry")
        add(entry, "TaxType", VAT_TAX_TYPE)
        add(entry, "Description", "Pridėtinės vertės mokestis")
        details = add(entry, "TaxCodeDetails")
        for tax in taxes.sorted("id"):
            detail = add(details, "TaxCodeDetail")
            add(detail, "TaxCode", self._saft_tax_code(tax))
            add(detail, "Description", txt(f"{tax.name} ({tax.type_tax_use})", 256))
            if tax.amount_type == "fixed":
                self._saft_add_amount_structure(
                    detail, "FlatTaxRate", tax.amount, options["currency"], tax.amount
                )
            else:
                add(detail, "TaxPercentage", decimal(tax.amount))
            add(detail, "Country", tax.country_id.code or "LT")
            add(detail, "STITaxCode", txt(tax.l10n_lt_tax_code or "PVM100", 24))

    @api.model
    def _saft_add_uom_table(self, options, ctx, master):
        if not ctx["uoms"]:
            return
        table = add(master, "UOMTable")
        for uom in ctx["uoms"].sorted("id"):
            entry = add(table, "UOMTableEntry")
            add(entry, "UnitOfMeasure", self._saft_uom_code(uom))
            add(entry, "Description", txt(uom.name, 256))

    @api.model
    def _saft_add_analysis_type_table(self, options, ctx, master):
        if not ctx["analytic_accounts"]:
            return
        table = add(master, "AnalysisTypeTable")
        for account in ctx["analytic_accounts"].sorted(lambda a: (a.plan_id.id, a.id)):
            entry = add(table, "AnalysisTypeTableEntry")
            add(entry, "AnalysisType", txt(account.plan_id.name, 24))
            add(entry, "AnalysisTypeDescription", txt(account.plan_id.name, 256))
            add(entry, "AnalysisID", txt(account.code or str(account.id), 35))
            add(entry, "AnalysisIDDescription", txt(account.name, 256))

    @api.model
    def _saft_add_movement_type_table(self, options, ctx, master):
        """Provided by l10n_lt_saft_export_stock."""

    @api.model
    def _saft_add_physical_stock(self, options, ctx, master):
        """Provided by l10n_lt_saft_export_stock."""

    @api.model
    def _saft_get_product_valuation_method(self, product):
        return ""

    @api.model
    def _saft_build_products(self, options, ctx):
        node = new_node("Products")
        company = options["company"]
        for product in ctx["products"].sorted("id"):
            product_node = add(node, "Product")
            add(product_node, "ProductCode", self._saft_product_code(product))
            add(
                product_node,
                "GoodsServicesID",
                "PS" if product.type == "service" else "PR",
            )
            add_opt(
                product_node, "ProductGroup", txt(product.categ_id.complete_name, 70)
            )
            add(product_node, "Description", txt(product.display_name, 256))
            add_opt(
                product_node,
                "ValuationMethod",
                txt(self._saft_get_product_valuation_method(product), 24),
            )
            add(product_node, "UOMBase", self._saft_uom_code(product.uom_id))
            ctx["uoms"] |= product.uom_id
            uoms = add(product_node, "UOMS")
            uom = add(uoms, "UOM")
            add(uom, "UOMStandard", self._saft_uom_code(product.uom_id))
            add(uom, "UOMToUOMBaseConversionFactor", "1")
            taxes = (product.taxes_id | product.supplier_taxes_id).filtered(
                lambda t: t.company_id == company
            )
            if taxes:
                taxes_node = add(product_node, "Taxes")
                for tax in taxes.flatten_taxes_hierarchy().sorted("id"):
                    ctx["taxes"] |= tax
                    tax_node = add(taxes_node, "Tax")
                    add(tax_node, "TaxType", VAT_TAX_TYPE)
                    add(tax_node, "TaxCode", self._saft_tax_code(tax))
        return node

    @api.model
    def _saft_get_assets(self, options):
        return self.env["l10n_lt.saft.asset"].search(
            [
                ("company_id", "=", options["company"].id),
                ("acquisition_date", "<=", options["date_to"]),
                "|",
                ("disposal_date", "=", False),
                ("disposal_date", ">=", options["date_from"]),
            ]
        )

    @api.model
    def _saft_add_assets(self, options, ctx, master, assets):
        node = add(master, "Assets")
        for asset in assets:
            values = asset._get_saft_period_values(
                options["date_from"], options["date_to"]
            )
            asset_node = add(node, "Asset")
            add(asset_node, "AssetID", txt(asset.code, 35))
            add(asset_node, "AccountID", self._saft_account_code(asset.account_id))
            add(asset_node, "Description", txt(asset.name, 256))
            add_opt(
                asset_node, "AssetsregistrationID", txt(asset.registration_number, 70)
            )
            if asset.partner_id:
                suppliers = add(asset_node, "Suppliers")
                self._saft_add_asset_supplier(suppliers, "Supplier", asset.partner_id)
            add(asset_node, "DateOfAcquisition", date_str(asset.acquisition_date))
            add(asset_node, "StartUpDate", date_str(asset.start_date))
            valuations = add(asset_node, "Valuations")
            valuation = add(valuations, "Valuation")
            add(valuation, "AssetValuationType", "IS")
            add_opt(valuation, "ValuationClass", txt(asset.valuation_class, 18))
            add(valuation, "AccountID", self._saft_account_code(asset.account_id))
            add(
                valuation,
                "AcquisitionAndProductionCostsBegin",
                amount(values["cost_begin"]),
            )
            add(
                valuation,
                "AcquisitionAndProductionCostsEnd",
                amount(values["cost_end"]),
            )
            add(valuation, "AssetLifeMonth", str(asset.useful_life_months))
            if values["disposal_date"]:
                add(valuation, "AssetDisposal", amount(values["disposal_amount"]))
                add(valuation, "AssetDisposalDate", date_str(values["disposal_date"]))
            add(valuation, "BookValueBegin", amount(values["book_value_begin"]))
            add(valuation, "DepreciationMethod", "T")
            add(
                valuation,
                "DepreciationPercentage",
                decimal(values["depreciation_percentage"]),
            )
            add(
                valuation,
                "DepreciationForPeriod",
                amount(values["depreciation_for_period"]),
            )
            if asset.depreciation_account_id:
                add(
                    valuation,
                    "AccountID2",
                    self._saft_account_code(asset.depreciation_account_id),
                )
            add(
                valuation,
                "AccumulatedDepreciation",
                amount(values["accumulated_depreciation"]),
            )
            add(valuation, "BookValueEnd", amount(values["book_value_end"]))

    @api.model
    def _saft_add_asset_supplier(self, parent, tag, partner):
        partner = partner.commercial_partner_id
        node = add(parent, tag)
        add(node, "SupplierName", txt(partner.name, 70))
        add(node, "SupplierID", self._saft_partner_code(partner))
        self._saft_add_address(node, "PostalAddress", partner)
        return node

    @api.model
    def _saft_add_owners(self, options, ctx, master):
        owners = options["company"].l10n_lt_saft_owner_ids
        if not owners:
            return
        node = add(master, "Owners")
        for owner in owners:
            partner = owner.partner_id
            owner_node = add(node, "Owner")
            add(
                owner_node,
                "OwnerID",
                txt(
                    owner.owner_code
                    or self._saft_registration_number(partner)
                    or partner.vat
                    or str(partner.id),
                    35,
                ),
            )
            add(owner_node, "OwnerName", txt(partner.name, 70))
            add(owner_node, "AccountID", self._saft_account_code(owner.account_id))
            if partner.city and partner.zip:
                self._saft_add_address(owner_node, "Address", partner)
            if owner.shares_quantity:
                add(owner_node, "SharesQuantity", quantity(owner.shares_quantity))
            if owner.shares_amount:
                add(owner_node, "SharesAmount", amount(owner.shares_amount))
            add_opt(owner_node, "SharesType", owner.shares_type)
            add_opt(
                owner_node,
                "SharesAcquisitionDate",
                owner.shares_acquisition_date
                and date_str(owner.shares_acquisition_date),
            )
            add_opt(
                owner_node,
                "SharesTransfersDate",
                owner.shares_transfer_date and date_str(owner.shares_transfer_date),
            )

    # -------------------------------------------------------------------------
    # 3. General ledger entries
    # -------------------------------------------------------------------------

    @api.model
    def _saft_get_period_moves(self, options, domain=None):
        return self.env["account.move"].search(
            [
                ("company_id", "=", options["company"].id),
                ("state", "=", "posted"),
                ("date", ">=", options["date_from"]),
                ("date", "<=", options["date_to"]),
            ]
            + (domain or []),
            order="date, name, id",
        )

    @api.model
    def _saft_build_general_ledger_entries(self, options, ctx):
        moves = self._saft_get_period_moves(options)
        lines = moves.line_ids
        node = new_node("GeneralLedgerEntries")
        add(node, "NumberOfEntries", str(len(moves)))
        add(node, "TotalDebit", amount(sum(lines.mapped("debit"))))
        add(node, "TotalCredit", amount(sum(lines.mapped("credit"))))
        if not moves:
            return node
        journals_node = add(node, "Journals")
        moves_by_journal = moves.grouped("journal_id")
        for journal in moves.journal_id.sorted(lambda j: j.code):
            journal_node = add(journals_node, "Journal")
            add(journal_node, "JournalID", txt(journal.code, 18))
            add(journal_node, "Description", txt(journal.name, 256))
            add(
                journal_node, "Type", txt(journal.l10n_lt_saft_journal_type or "KT", 24)
            )
            transactions = add(journal_node, "Transactions")
            for move in moves_by_journal[journal]:
                self._saft_add_transaction(options, ctx, transactions, move)
        return node

    @api.model
    def _saft_add_transaction(self, options, ctx, parent, move):
        period, period_year = self._saft_period(options, move.date)
        node = add(parent, "Transaction")
        add(node, "TransactionID", txt(move.name, 70))
        add(node, "Period", str(period))
        add(node, "PeriodYear", str(period_year))
        add(node, "TransactionDate", date_str(move.invoice_date or move.date))
        add(node, "Description", txt(move.ref or move.name, 256))
        add(node, "SystemEntryDate", datetime_str(move.create_date))
        add(node, "GLPostingDate", date_str(move.date))
        partner = move.commercial_partner_id or move.line_ids.partner_id[:1]
        is_supplier = move.is_purchase_document(include_receipts=True) or (
            not move.is_sale_document(include_receipts=True)
            and any(
                line.account_id.account_type == "liability_payable"
                for line in move.line_ids
            )
        )
        if partner:
            self._saft_add_partner_reference(node, ctx, partner, is_supplier)
        else:
            add(node, "CustomerID", "")
        add(node, "SystemID", str(move.id))
        lines_node = add(node, "Lines")
        for line in move.line_ids.sorted("id"):
            self._saft_add_transaction_line(options, ctx, lines_node, line)

    @api.model
    def _saft_add_transaction_line(self, options, ctx, parent, line):
        node = add(parent, "Line")
        add(node, "RecordID", str(line.id))
        add(node, "AccountID", self._saft_account_code(line.account_id))
        ctx["accounts"] |= line.account_id
        if line.analytic_distribution:
            self._saft_add_analysis(add(node, "Analyses"), ctx, line)
        if line.move_id.is_invoice(include_receipts=True):
            add(node, "SourceDocumentID", txt(line.move_id.name, 35))
        if line.partner_id:
            self._saft_add_partner_reference(
                node, ctx, line.partner_id, self._saft_is_supplier_line(line)
            )
        else:
            add(node, "CustomerID", "")
        add(
            node,
            "Description",
            txt(line.name or line.move_id.ref or line.move_id.name, 256),
        )
        tag = "CreditAmount" if line.balance < 0 else "DebitAmount"
        self._saft_add_amount_structure(
            node, tag, line.balance, line.currency_id, line.amount_currency
        )
        if line.tax_line_id:
            ctx["taxes"] |= line.tax_line_id
            infos = add(node, "TaxInformations")
            self._saft_add_tax_information(
                infos,
                "TaxInformation",
                line.tax_line_id,
                line.tax_base_amount,
                line.balance,
                line.currency_id,
                line.amount_currency,
            )

    # -------------------------------------------------------------------------
    # 4. Source documents
    # -------------------------------------------------------------------------

    @api.model
    def _saft_build_source_documents(self, options, ctx):
        node = new_node("SourceDocuments")
        if self._saft_has(options, "SalesInvoices"):
            self._saft_add_invoices(options, ctx, node, is_sale=True)
        if self._saft_has(options, "PurchaseInvoices"):
            self._saft_add_invoices(options, ctx, node, is_sale=False)
        if self._saft_has(options, "Payments"):
            self._saft_add_payments(options, ctx, node)
        if self._saft_has(options, "MovementOfGoods"):
            self._saft_add_movement_of_goods(options, ctx, node)
        if self._saft_has(options, "AssetTransactions"):
            self._saft_add_asset_transactions(options, ctx, node)
        return node

    @api.model
    def _saft_get_invoice_type(self, move):
        has_vat = bool(
            move.company_id.vat
            if move.is_sale_document()
            else move.commercial_partner_id.vat
        )
        if "debit_origin_id" in move._fields and move.debit_origin_id:
            return "DS" if has_vat else "D"
        if move.move_type in ("out_refund", "in_refund"):
            return "KS" if has_vat else "K"
        return "SF" if has_vat else "S"

    @api.model
    def _saft_get_invoice_lines(self, move):
        lines = move.invoice_line_ids.filtered(lambda l: l.display_type == "product")
        return lines or move.line_ids.filtered(
            lambda l: (
                l.display_type
                not in ("payment_term", "tax", "line_section", "line_note")
            )
        )

    @api.model
    def _saft_add_invoices(self, options, ctx, parent, is_sale):
        move_types = (
            ("out_invoice", "out_refund") if is_sale else ("in_invoice", "in_refund")
        )
        moves = self._saft_get_period_moves(options, [("move_type", "in", move_types)])
        if not moves:
            return
        node = add(parent, "SalesInvoices" if is_sale else "PurchaseInvoices")
        add(node, "NumberOfEntries", str(len(moves)))
        total_debit_node = add(node, "TotalDebit")
        total_credit_node = add(node, "TotalCredit")
        totals = {"D": 0.0, "K": 0.0}
        for move in moves:
            self._saft_add_invoice(options, ctx, node, move, is_sale, totals)
        total_debit_node.text = amount(totals["D"])
        total_credit_node.text = amount(totals["K"])

    @api.model
    def _saft_add_invoice(self, options, ctx, parent, move, is_sale, totals):
        currency = move.currency_id
        partner = move.commercial_partner_id
        node = add(parent, "Invoice")
        add(node, "InvoiceNo", txt(move.name, 70))
        info = add(node, "CustomerInfo" if is_sale else "SupplierInfo")
        self._saft_add_partner_reference(info, ctx, partner, not is_sale)
        add(
            info,
            "TaxRegistrationNumber",
            txt(partner.vat if partner.vat != "/" else "", 35),
        )
        add(info, "TaxType", VAT_TAX_TYPE if partner.vat and partner.vat != "/" else "")
        add(info, "Country", partner.country_id.code or "")
        term_line = move.line_ids.filtered(lambda l: l.display_type == "payment_term")[
            :1
        ]
        add(
            node,
            "AccountID",
            self._saft_account_code(
                term_line.account_id or partner.property_account_receivable_id
            ),
        )
        add(node, "InvoiceDate", date_str(move.invoice_date or move.date))
        add(node, "InvoiceType", self._saft_get_invoice_type(move))
        add(node, "GLPostingDate", date_str(move.date))
        add(node, "SystemID", str(move.id))
        add(node, "TransactionID", txt(move.name, 70))
        tax_point_date = (
            move.taxable_supply_date
            or move.delivery_date
            or move.invoice_date
            or move.date
        )
        for number, line in enumerate(self._saft_get_invoice_lines(move), start=1):
            indicator = "K" if line.balance < 0 else "D"
            totals[indicator] += abs(line.balance)
            line_node = add(node, "Line")
            add(line_node, "LineNumber", str(number))
            add(line_node, "AccountID", self._saft_account_code(line.account_id))
            ctx["accounts"] |= line.account_id
            self._saft_add_analysis(line_node, ctx, line)
            if line.product_id:
                ctx["products"] |= line.product_id
                add(
                    line_node,
                    "GoodsServicesID",
                    "PS" if line.product_id.type == "service" else "PR",
                )
                add(line_node, "ProductCode", self._saft_product_code(line.product_id))
            if line.quantity:
                add(line_node, "Quantity", quantity(line.quantity))
            if line.product_uom_id:
                ctx["uoms"] |= line.product_uom_id
                add(line_node, "InvoiceUOM", self._saft_uom_code(line.product_uom_id))
            unit_price = (
                abs(line.balance) / abs(line.quantity)
                if line.quantity
                else abs(line.balance)
            )
            add(line_node, "UnitPrice", decimal(unit_price, 10))
            add(line_node, "TaxPointDate", date_str(tax_point_date))
            add(
                line_node,
                "Description",
                txt(line.name or line.product_id.display_name or move.name, 256),
            )
            self._saft_add_amount_structure(
                line_node,
                "InvoiceLineAmount",
                line.balance,
                currency,
                line.amount_currency,
            )
            add(line_node, "DebitCreditIndicator", indicator)
            for tax in line.tax_ids.flatten_taxes_hierarchy():
                ctx["taxes"] |= tax
                if tax.amount_type == "fixed":
                    tax_amount = tax.amount * abs(line.quantity)
                    tax_amount_currency = tax_amount
                else:
                    tax_amount = abs(line.balance) * tax.amount / 100.0
                    tax_amount_currency = abs(line.amount_currency) * tax.amount / 100.0
                self._saft_add_tax_information(
                    line_node,
                    "TaxInformation",
                    tax,
                    line.balance,
                    float_round(tax_amount, 2),
                    currency,
                    float_round(tax_amount_currency, 2),
                )
        if move.move_type in ("out_refund", "in_refund") and (
            move.reversed_entry_id or move.ref
        ):
            references = add(node, "References")
            credit_note = add(references, "CreditNote")
            add_opt(credit_note, "Reference", txt(move.reversed_entry_id.name, 35))
            add_opt(credit_note, "Reason", txt(move.ref, 256))
        document_totals = add(node, "DocumentTotals")
        for tax_line in move.line_ids.filtered("tax_line_id"):
            ctx["taxes"] |= tax_line.tax_line_id
            self._saft_add_tax_information(
                document_totals,
                "TaxInformationTotals",
                tax_line.tax_line_id,
                tax_line.tax_base_amount,
                tax_line.balance,
                currency,
                tax_line.amount_currency,
            )
        add(document_totals, "NetTotal", amount(move.amount_untaxed_signed))
        add(document_totals, "GrossTotal", amount(move.amount_total_signed))
        add(node, "GLTransactionID", txt(move.name, 70))

    @api.model
    def _saft_get_payment_moves(self, options):
        return self._saft_get_period_moves(
            options, [("journal_id.type", "in", ("bank", "cash", "credit"))]
        )

    @api.model
    def _saft_get_payment_method(self, move):
        if move.journal_id.type == "cash":
            liquidity = move.line_ids.filtered(
                lambda l: (
                    l.account_id.account_type in ("asset_cash", "liability_credit_card")
                )
            )
            return "KPO" if sum(liquidity.mapped("balance")) >= 0 else "KIO"
        return "BP"

    @api.model
    def _saft_get_source_document(self, line):
        partials = line.matched_debit_ids | line.matched_credit_ids
        counterparts = (partials.debit_move_id | partials.credit_move_id) - line
        invoices = counterparts.move_id.filtered(
            lambda m: m.is_invoice(include_receipts=True)
        )
        return invoices[:1].name or ""

    @api.model
    def _saft_add_payments(self, options, ctx, parent):
        moves = self._saft_get_payment_moves(options)
        if not moves:
            return
        node = add(parent, "Payments")
        add(node, "NumberOfEntries", str(len(moves)))
        add(node, "TotalDebit", amount(sum(moves.line_ids.mapped("debit"))))
        add(node, "TotalCredit", amount(sum(moves.line_ids.mapped("credit"))))
        for move in moves:
            _period, period_year = self._saft_period(options, move.date)
            payment = add(node, "Payment")
            add(payment, "PaymentRefNo", txt(move.name, 35))
            add(payment, "PeriodYear", str(period_year))
            add(payment, "TransactionID", txt(move.name, 70))
            add(payment, "TransactionDate", date_str(move.date))
            add(payment, "PaymentMethod", self._saft_get_payment_method(move))
            self._saft_add_bank_accounts(payment, move.journal_id.bank_account_id)
            add(payment, "Description", txt(move.ref or move.name, 256))
            add(payment, "SystemID", str(move.id))
            lines_node = add(payment, "Lines")
            for number, line in enumerate(move.line_ids.sorted("id"), start=1):
                line_node = add(lines_node, "Line")
                add(line_node, "LineNumber", str(number))
                add_opt(
                    line_node,
                    "SourceDocumentID",
                    txt(self._saft_get_source_document(line), 35),
                )
                add(line_node, "AccountID", self._saft_account_code(line.account_id))
                ctx["accounts"] |= line.account_id
                if line.analytic_distribution:
                    self._saft_add_analysis(add(line_node, "Analyses"), ctx, line)
                partner = line.partner_id.commercial_partner_id
                is_supplier = bool(partner) and self._saft_is_supplier_line(line)
                if partner:
                    ctx["suppliers" if is_supplier else "customers"] |= partner
                add(
                    line_node,
                    "CustomerID",
                    self._saft_partner_code(partner)
                    if partner and not is_supplier
                    else "",
                )
                add(
                    line_node,
                    "SupplierID",
                    self._saft_partner_code(partner) if is_supplier else "",
                )
                add_opt(line_node, "Description", txt(line.name, 256))
                add(line_node, "DebitCreditIndicator", "K" if line.balance < 0 else "D")
                self._saft_add_amount_structure(
                    line_node,
                    "PaymentLineAmount",
                    line.balance,
                    line.currency_id,
                    line.amount_currency,
                )
            add(payment, "GrossTotal", amount(sum(move.line_ids.mapped("debit"))))

    @api.model
    def _saft_add_movement_of_goods(self, options, ctx, parent):
        """Provided by l10n_lt_saft_export_stock."""

    @api.model
    def _saft_add_asset_transactions(self, options, ctx, parent):
        transactions = []
        for asset in self._saft_get_assets(options):
            transactions += [
                (asset, values)
                for values in asset._get_saft_transactions(
                    options["date_from"], options["date_to"]
                )
            ]
        if not transactions:
            return
        node = add(parent, "AssetTransactions")
        add(node, "NumberOfAssetTransactions", str(len(transactions)))
        for asset, values in transactions:
            transaction = add(node, "AssetTransaction")
            add(transaction, "AssetTransactionID", txt(values["id"], 70))
            add(transaction, "AssetID", txt(asset.code, 35))
            add(transaction, "AssetTransactionType", values["type"])
            add_opt(transaction, "Description", txt(values["description"], 256))
            add(transaction, "AssetTransactionDate", date_str(values["date"]))
            if values["type"] == "I" and asset.partner_id:
                self._saft_add_asset_supplier(transaction, "Supplier", asset.partner_id)
                ctx["suppliers"] |= asset.partner_id.commercial_partner_id
            add(transaction, "TransactionID", txt(values["transaction_id"], 70))
            valuations = add(transaction, "AssetTransactionValuations")
            valuation = add(valuations, "AssetTransactionValuation")
            add(valuation, "AssetValuationType", "IS")
            add(
                valuation,
                "AcquisitionAndProductionCostsOnTransaction",
                amount(values["cost"]),
            )
            add(valuation, "BookValueOnTransaction", amount(values["book_value"]))
            add(valuation, "AssetTransactionAmount", amount(values["amount"]))
            add(valuation, "DebitCreditIndicator", values["indicator"])

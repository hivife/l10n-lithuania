from datetime import date

from freezegun import freeze_time
from lxml import etree

from odoo import Command
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.l10n_lt_saft_export.models.l10n_lt_saft_export import NS

NSMAP = {"s": NS}


@tagged("post_install_l10n", "post_install", "-at_install")
class TestSaftStock(AccountTestInvoicingCommon):
    @classmethod
    @AccountTestInvoicingCommon.setup_country("lt")
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.write(
            {
                "vat": "LT123456715",
                "city": "Vilnius",
                "zip": "01100",
                "phone": "+37052000000",
            }
        )
        cls.company.partner_id._set_additional_identifier("LT_JAK", "304123456")
        cls.warehouse = cls.env["stock.warehouse"].search(
            [("company_id", "=", cls.company.id)], limit=1
        )
        cls.customer = cls.env["res.partner"].create(
            {
                "name": "UAB Pirkėjas",
                "city": "Kaunas",
                "zip": "44001",
                "country_id": cls.env.ref("base.lt").id,
            }
        )
        cls.supplier = cls.env["res.partner"].create(
            {
                "name": "Lieferant GmbH",
                "city": "Berlin",
                "zip": "10115",
                "country_id": cls.env.ref("base.de").id,
            }
        )
        category = cls.env["product.category"].create(
            {"name": "FIFO goods", "property_cost_method": "fifo"}
        )
        cls.product = cls.env["product.product"].create(
            {
                "name": "Widget",
                "default_code": "WDG-1",
                "is_storable": True,
                "categ_id": category.id,
                "standard_price": 10.0,
            }
        )

        with freeze_time("2024-12-10"):
            cls.opening_receipt = cls._transfer(
                cls.warehouse.in_type_id, cls.supplier, 10
            )
        with freeze_time("2025-02-01"):
            cls.receipt = cls._transfer(cls.warehouse.in_type_id, cls.supplier, 5)
        with freeze_time("2025-03-01"):
            cls.delivery = cls._transfer(cls.warehouse.out_type_id, cls.customer, 8)
        with freeze_time("2025-04-01"):
            # Odoo 20: a scrap is a stock.move flagged is_scrap
            cls.scrap = cls.env["stock.move"].create(
                {
                    "is_scrap": True,
                    "product_id": cls.product.id,
                    "quantity": 1.0,
                    "location_id": cls.warehouse.lot_stock_id.id,
                    "location_dest_id": cls.company.scrap_location_id.id,
                    "company_id": cls.company.id,
                }
            )
            cls.scrap._action_scrap()

    @classmethod
    def _transfer(cls, picking_type, partner, qty):
        src = picking_type.default_location_src_id or cls.env.ref(
            "stock.stock_location_suppliers"
        )
        dest = picking_type.default_location_dest_id or cls.env.ref(
            "stock.stock_location_customers"
        )
        picking = cls.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "partner_id": partner.id,
                "location_id": src.id,
                "location_dest_id": dest.id,
                "move_ids": [
                    Command.create(
                        {
                            "product_id": cls.product.id,
                            "product_uom_qty": qty,
                            "location_id": src.id,
                            "location_dest_id": dest.id,
                        }
                    )
                ],
            }
        )
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = qty
            move.picked = True
        picking.button_validate()
        return picking

    def _generate(self, data_type):
        exporter = self.env["l10n_lt.saft.export"]
        options = exporter._saft_get_options(
            self.company, date(2025, 1, 1), date(2025, 12, 31), data_type=data_type
        )
        content = exporter._saft_generate(options)
        self.assertEqual(
            exporter._saft_validate(content),
            [],
            f"{data_type} file is not valid against the XSD",
        )
        return etree.fromstring(content)

    def _values(self, tree, xpath):
        return [node.text or "" for node in tree.xpath(xpath, namespaces=NSMAP)]

    def test_movement_of_goods(self):
        tree = self._generate("MG")
        mog = "//s:SourceDocuments/s:MovementOfGoods"
        self.assertEqual(self._values(tree, f"{mog}/s:NumberOfMovementLines"), ["3"])
        self.assertEqual(self._values(tree, f"{mog}/s:TotalQuantityReceived"), ["5"])
        self.assertEqual(self._values(tree, f"{mog}/s:TotalQuantityIssued"), ["9"])
        self.assertEqual(
            self._values(tree, f"{mog}/s:StockMovement/s:MovementType"),
            ["PIR", "PARD", "N"],
        )
        self.assertEqual(
            self._values(tree, f"{mog}//s:Line/s:DebitCreditIndicator"), ["D", "K", "K"]
        )
        self.assertEqual(
            self._values(tree, f"{mog}//s:Line/s:BookValue"),
            ["50.00", "80.00", "10.00"],
        )
        self.assertEqual(
            self._values(tree, f"{mog}/s:StockMovement/s:MovementReference")[:2],
            [self.receipt.name, self.delivery.name],
        )
        receipt_line = tree.xpath(
            f"{mog}/s:StockMovement[1]/s:Lines/s:Line", namespaces=NSMAP
        )[0]
        self.assertEqual(
            self._values(receipt_line, "s:SupplierID"), [str(self.supplier.id)]
        )
        self.assertEqual(self._values(receipt_line, "s:CustomerID"), [""])
        self.assertEqual(
            self._values(receipt_line, "s:ShipsTo/s:ShipTo/s:WarehouseID"),
            [self.warehouse.code],
        )

    def test_master_files(self):
        tree = self._generate("MG")
        self.assertEqual(
            self._values(
                tree, "//s:MovementTypeTable/s:MovementTypeTableEntry/s:MovementType"
            ),
            ["PARD", "PIR", "N"],
        )
        product = '//s:Products/s:Product[s:ProductCode="WDG-1"]'
        self.assertEqual(self._values(tree, f"{product}/s:ValuationMethod"), ["FIFO"])
        self.assertEqual(self._values(tree, f"{product}/s:GoodsServicesID"), ["PR"])
        stock = '//s:PhysicalStock/s:PhysicalStockEntry[s:ProductCode="WDG-1"]'
        self.assertEqual(self._values(tree, f"{stock}/s:OpeningStockQuantity"), ["10"])
        self.assertEqual(self._values(tree, f"{stock}/s:OpeningStockValue"), ["100.00"])
        self.assertEqual(self._values(tree, f"{stock}/s:ClosingStockQuantity"), ["6"])
        self.assertEqual(self._values(tree, f"{stock}/s:ClosingStockValue"), ["60.00"])
        self.assertIn(
            str(self.customer.id),
            self._values(tree, "//s:Customers/s:Customer/s:CustomerID"),
        )
        self.assertIn(
            str(self.supplier.id),
            self._values(tree, "//s:Suppliers/s:Supplier/s:SupplierID"),
        )
        uoms = set(self._values(tree, "//s:UOMTable/s:UOMTableEntry/s:UnitOfMeasure"))
        self.assertIn(self.product.uom_id.name, uoms)

    def test_full_file_is_valid(self):
        tree = self._generate("F")
        self.assertTrue(tree.xpath("//s:MovementOfGoods", namespaces=NSMAP))
        self.assertTrue(tree.xpath("//s:PhysicalStock", namespaces=NSMAP))
        # stock parts are not part of the other split files
        for data_type in ("GL", "PA", "AS"):
            with self.subTest(data_type=data_type):
                tree = self._generate(data_type)
                self.assertFalse(
                    tree.xpath(
                        "//s:MovementOfGoods | //s:PhysicalStock", namespaces=NSMAP
                    )
                )

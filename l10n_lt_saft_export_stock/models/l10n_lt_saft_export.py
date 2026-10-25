from collections import defaultdict
from datetime import datetime, time

from dateutil.relativedelta import relativedelta

from odoo import api, models
from odoo.tools import float_is_zero

from odoo.addons.l10n_lt_saft_export.models.l10n_lt_saft_export import (
    add,
    add_opt,
    amount,
    date_str,
    decimal,
    quantity,
    signed_amount,
    txt,
)

MOVEMENT_TYPES = {
    "PARD": "Pardavimas",
    "PIR": "Pirkimas",
    "PP": "Pagaminta produkcija",
    "PG": "Pardavimo grąžinimas",
    "PRG": "Pirkimo grąžinimas",
    "VP": "Vidinis perkėlimas",
    "N": "Nurašymas",
    "KT": "Kita",
}

VALUATION_METHODS = {
    "fifo": "FIFO",
    "average": "AVG",
    "standard": "STANDARD",
}

INTERNAL_USAGES = ("internal", "transit")


class L10nLtSaftExport(models.AbstractModel):
    _inherit = "l10n_lt.saft.export"

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @api.model
    def _saft_stock_datetime_bounds(self, options):
        start = datetime.combine(options["date_from"], time.min)
        end = datetime.combine(options["date_to"] + relativedelta(days=1), time.min)
        return start, end

    @api.model
    def _saft_stock_moves_domain(self, options):
        return [
            ("company_id", "=", options["company"].id),
            ("state", "=", "done"),
            ("product_id.is_storable", "=", True),
        ]

    @api.model
    def _saft_get_movement_type(self, move):
        src, dest = move.location_id.usage, move.location_dest_id.usage
        src_internal, dest_internal = src in INTERNAL_USAGES, dest in INTERNAL_USAGES
        if src_internal and dest_internal:
            return "VP"
        if dest_internal:
            return {
                "supplier": "PIR",
                "customer": "PG",
                "production": "PP",
            }.get(src, "KT")
        if src_internal:
            if dest == "customer":
                return "PARD"
            if dest == "supplier":
                return "PRG"
            if move.is_scrap or dest == "inventory":
                return "N"
        return "KT"

    @api.model
    def _saft_get_movement_sub_type(self, move):
        if move.is_scrap:
            return "SCRAP"
        if move.is_inventory:
            return "INVENTORY"
        return txt(move.picking_type_id.name or move.location_dest_id.usage, 24).upper()

    @api.model
    def _saft_stock_direction(self, move):
        """+1 entering the company's stock, -1 leaving it, 0 for internal moves."""
        src_internal = move.location_id.usage in INTERNAL_USAGES
        dest_internal = move.location_dest_id.usage in INTERNAL_USAGES
        if dest_internal and not src_internal:
            return 1
        if src_internal and not dest_internal:
            return -1
        return 0

    @api.model
    def _saft_get_product_valuation_method(self, product):
        return VALUATION_METHODS.get(
            product.cost_method, super()._saft_get_product_valuation_method(product)
        )

    @api.model
    def _saft_get_stock_account(self, product):
        return product.with_company(self.env.company)._get_product_accounts()[
            "stock_valuation"
        ]

    # -------------------------------------------------------------------------
    # Master files
    # -------------------------------------------------------------------------

    @api.model
    def _saft_add_movement_type_table(self, options, ctx, master):
        movement_types = ctx.get("movement_types")
        if not movement_types:
            return
        table = add(master, "MovementTypeTable")
        for movement_type, description in MOVEMENT_TYPES.items():
            if movement_type in movement_types:
                entry = add(table, "MovementTypeTableEntry")
                add(entry, "MovementType", movement_type)
                add(entry, "Description", description)

    @api.model
    def _saft_get_physical_stock(self, options, products):
        """Opening / closing quantity and value per product."""
        start, end = self._saft_stock_datetime_bounds(options)
        result = defaultdict(
            lambda: {
                "opening_qty": 0.0,
                "opening_value": 0.0,
                "closing_qty": 0.0,
                "closing_value": 0.0,
            }
        )
        moves = self.env["stock.move"].search(
            self._saft_stock_moves_domain(options)
            + [("product_id", "in", products.ids), ("date", "<", end)]
        )
        for move in moves:
            direction = self._saft_stock_direction(move)
            if not direction:
                continue
            values = result[move.product_id]
            qty = direction * move.quantity_product_uom
            values["closing_qty"] += qty
            values["closing_value"] += move.value  # signed: positive in, negative out
            if move.date < start:
                values["opening_qty"] += qty
                values["opening_value"] += move.value
        return result

    @api.model
    def _saft_add_physical_stock(self, options, ctx, master):
        products = ctx["products"].filtered("is_storable")
        if options["data_type"] in ("F", "MG"):
            products |= (
                self.env["stock.move"]
                .search(
                    self._saft_stock_moves_domain(options)
                    + [("date", "<", self._saft_stock_datetime_bounds(options)[1])],
                )
                .product_id
            )
        if not products:
            return
        stock = self._saft_get_physical_stock(options, products)
        node = add(master, "PhysicalStock")
        for product in products.sorted("id"):
            values = stock[product]
            if all(float_is_zero(v, precision_digits=4) for v in values.values()):
                continue
            ctx["products"] |= product
            ctx["uoms"] |= product.uom_id
            entry = add(node, "PhysicalStockEntry")
            add(entry, "ProductCode", self._saft_product_code(product))
            add(entry, "UOMPhysicalStock", self._saft_uom_code(product.uom_id))
            add(entry, "UOMToUOMBaseConversionFactor", "1")
            if values["closing_qty"] > 0:
                add(
                    entry,
                    "UnitPrice",
                    decimal(abs(values["closing_value"] / values["closing_qty"]), 10),
                )
            add(entry, "OpeningStockQuantity", decimal(values["opening_qty"], 10))
            add(entry, "OpeningStockValue", signed_amount(values["opening_value"]))
            add(entry, "ClosingStockQuantity", decimal(values["closing_qty"], 10))
            add(entry, "ClosingStockValue", signed_amount(values["closing_value"]))
        if not len(node):
            master.remove(node)

    @api.model
    def _saft_build_master_files(self, options, ctx):
        # Physical stock may add products, so they are collected before the
        # products are written (the base calls the hooks in XSD order).
        if self._saft_has(options, "PhysicalStock") and options["data_type"] in (
            "F",
            "MG",
        ):
            ctx["products"] |= (
                self.env["stock.move"]
                .search(
                    self._saft_stock_moves_domain(options)
                    + [("date", "<", self._saft_stock_datetime_bounds(options)[1])],
                )
                .product_id
            )
        return super()._saft_build_master_files(options, ctx)

    # -------------------------------------------------------------------------
    # Source documents
    # -------------------------------------------------------------------------

    @api.model
    def _saft_add_movement_of_goods(self, options, ctx, parent):
        start, end = self._saft_stock_datetime_bounds(options)
        moves = self.env["stock.move"].search(
            self._saft_stock_moves_domain(options)
            + [("date", ">=", start), ("date", "<", end)],
            order="date, id",
        )
        if not moves:
            return
        groups = defaultdict(lambda: self.env["stock.move"])
        for move in moves:
            groups[move.picking_id or move.reference or str(move.id)] |= move

        node = add(parent, "MovementOfGoods")
        add(node, "NumberOfMovementLines", str(len(moves)))
        received = sum(
            m.quantity_product_uom for m in moves if self._saft_stock_direction(m) > 0
        )
        issued = sum(
            m.quantity_product_uom for m in moves if self._saft_stock_direction(m) < 0
        )
        add(node, "TotalQuantityReceived", quantity(received))
        add(node, "TotalQuantityIssued", quantity(issued))
        ctx.setdefault("movement_types", set())
        for key, group in groups.items():
            self._saft_add_stock_movement(options, ctx, node, key, group)

    @api.model
    def _saft_add_stock_movement(self, options, ctx, parent, key, moves):
        picking = moves.picking_id[:1]
        first = moves[0]
        movement_type = self._saft_get_movement_type(first)
        ctx["movement_types"].add(movement_type)
        reference = picking.name or first.reference or str(first.id)
        account_move = moves.account_move_id[:1]

        node = add(parent, "StockMovement")
        add(node, "MovementReference", txt(reference, 35))
        add(node, "MovementDate", date_str((picking.date_done or first.date).date()))
        if account_move:
            add(node, "MovementPostingDate", date_str(account_move.date))
        add(node, "MovementType", movement_type)
        add(node, "SystemID", str(picking.id if picking else first.id))
        if picking:
            document = add(node, "DocumentReference")
            add(
                document,
                "DocumentType",
                txt(picking.picking_type_id.code or "internal", 18).upper(),
            )
            add(document, "DocumentNumber", txt(picking.origin or picking.name, 35))
        lines = add(node, "Lines")
        partner = (picking.partner_id or first.partner_id).commercial_partner_id
        for number, move in enumerate(moves, start=1):
            line_type = self._saft_get_movement_type(move)
            ctx["movement_types"].add(line_type)
            direction = self._saft_stock_direction(move)
            is_supplier = line_type in ("PIR", "PRG")
            if partner:
                ctx["suppliers" if is_supplier else "customers"] |= partner
            product = move.product_id
            ctx["products"] |= product
            ctx["uoms"] |= product.uom_id
            stock_account = self._saft_get_stock_account(product)
            if stock_account:
                ctx["accounts"] |= stock_account

            line = add(lines, "Line")
            add(line, "LineNumber", str(number))
            add(
                line,
                "AccountID",
                self._saft_account_code(stock_account) if stock_account else "",
            )
            add(line, "TransactionID", txt(move.account_move_id.name or reference, 70))
            add(
                line,
                "CustomerID",
                self._saft_partner_code(partner) if partner and not is_supplier else "",
            )
            add(
                line,
                "SupplierID",
                self._saft_partner_code(partner) if partner and is_supplier else "",
            )
            warehouse = (
                move.location_dest_id.warehouse_id
                if direction >= 0
                else move.location_id.warehouse_id
            )
            if warehouse:
                ships = add(line, "ShipsTo" if direction >= 0 else "ShipsFrom")
                ship = add(ships, "ShipTo" if direction >= 0 else "ShipFrom")
                add(ship, "DeliveryDate", date_str(move.date.date()))
                add(ship, "WarehouseID", txt(warehouse.code, 35))
            add(line, "ProductCode", self._saft_product_code(product))
            add(line, "Quantity", quantity(move.quantity_product_uom))
            add(line, "UnitOfMeasure", self._saft_uom_code(product.uom_id))
            add(line, "UOMToUOMPhysicalStockConversionFactor", "1")
            add(line, "BookValue", amount(move.value))
            add(line, "MovementSubType", self._saft_get_movement_sub_type(move))
            add_opt(
                line,
                "MovementComments",
                txt(move.description_picking or move.origin, 256),
            )
            add(line, "MovementType", line_type)
            add(line, "DebitCreditIndicator", "K" if direction < 0 else "D")
        if account_move:
            add(node, "GLTransactionID", txt(account_move.name, 70))

- The account classifier codes of VMI (Annex 2 of the SAF-T description) are not
  pre-filled for the Lithuanian chart of accounts yet.
- A file is always generated in one part (`NumberOfParts` = `PartNumber` = 1);
  very large files are not split into several parts.
- Ship-to / ship-from data, order references and settlement discounts of invoices
  are not exported.
- Line level tax amounts of invoices are computed from the tax rate; the document
  totals use the posted tax lines.
- The asset register is intentionally minimal (straight-line, monthly depreciation)
  and does not post depreciation entries.
- The i.SAF monthly invoice register is not part of this module.

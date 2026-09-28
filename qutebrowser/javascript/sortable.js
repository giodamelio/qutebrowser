// SPDX-FileCopyrightText: Giovanni d'Amelio <gio@damelio.net>
//
// SPDX-License-Identifier: GPL-3.0-or-later

"use strict";

// A cell's data-sort attribute overrides its text as the value to sort by.
function sortValue(row, column) {
    const cell = row.cells[column];
    return cell.dataset.sort ?? cell.textContent.trim();
}

function sortTable(table, column, header) {
    const descending = header.getAttribute("aria-sort") === "ascending";
    for (const other of table.tHead.rows[0].cells) {
        other.removeAttribute("aria-sort");
    }
    header.setAttribute("aria-sort", descending ? "descending" : "ascending");
    const body = table.tBodies[0];
    const rows = Array.from(body.rows).sort((first, second) => {
        const order = sortValue(first, column).localeCompare(
            sortValue(second, column), undefined, {"numeric": true});
        return descending ? -order : order;
    });
    body.append(...rows);
}

document.addEventListener("DOMContentLoaded", () => {
    for (const table of document.querySelectorAll("table.sortable")) {
        Array.from(table.tHead.rows[0].cells).forEach((header, column) => {
            header.addEventListener("click", () => sortTable(table, column, header));
        });
    }
});

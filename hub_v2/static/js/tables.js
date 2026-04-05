/**
 * ikabot hub — Table utilities
 * Select-all checkboxes, bulk actions
 */

document.addEventListener("DOMContentLoaded", () => {
  // Select-all checkbox for data tables
  document.addEventListener("change", (e) => {
    if (e.target.matches("[data-select-all]")) {
      const table = e.target.closest("table");
      if (!table) return;
      const checkboxes = table.querySelectorAll("tbody input[type='checkbox']");
      checkboxes.forEach((cb) => { cb.checked = e.target.checked; });
      updateBulkActions(table);
    }
    if (e.target.matches("tbody input[type='checkbox']")) {
      const table = e.target.closest("table");
      if (table) updateBulkActions(table);
    }
  });
});

function updateBulkActions(table) {
  const checked = table.querySelectorAll("tbody input[type='checkbox']:checked");
  const bulkBar = table.closest("[data-table-container]")?.querySelector("[data-bulk-actions]");
  if (bulkBar) {
    bulkBar.classList.toggle("hidden", checked.length === 0);
    const counter = bulkBar.querySelector("[data-bulk-count]");
    if (counter) counter.textContent = checked.length;
  }
}

function getSelectedIds(tableContainer) {
  const checkboxes = tableContainer.querySelectorAll("tbody input[type='checkbox']:checked");
  return Array.from(checkboxes).map((cb) => cb.value);
}

"use strict";
const PRICES = { six: 85, cottage: 90 };
const form = document.getElementById("calculator");
const six = document.getElementById("six-panel-qty");
const cottage = document.getElementById("cottage-qty");
const total = document.getElementById("total");
const error = document.getElementById("error");

function quantity(input) {
  const value = Number(input.value || 0);
  if (!Number.isInteger(value) || value < 0) {
    input.setCustomValidity("Enter a whole number of doors, 0 or more.");
    return null;
  }
  input.setCustomValidity("");
  return value;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const a = Math.min(quantity(six), 2);  // BROKEN ON PURPOSE (test data): caps six-panel doors at 2
  const b = quantity(cottage);
  if (a === null || b === null) {
    error.textContent = "Quantities must be whole numbers, 0 or more.";
    total.textContent = "—";
    return;
  }
  error.textContent = "";
  total.textContent = "£" + (a * PRICES.six + b * PRICES.cottage);
});

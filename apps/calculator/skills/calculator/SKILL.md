---
name: calculator
description: How to work out a sum on the device's Calculator and show it on its display. Use this when the user wants a calculation done on the Calculator or shown on the screen.
---

# Calculator

The Calculator keys in an expression and shows the expression and the result.

1. `calculator_calculate` with `expression` such as `12*(3+4)` clears the display, keys the expression in and presses `=`. It returns the display, e.g. `12*(3+4)= 84`.
2. `calculator_get_display` reads the display without changing it; `calculator_clear` clears it.

Operators: `+ - * / %` and brackets (`x%` means x/100). `÷ × −` and a decimal comma are understood too. An expression the Calculator cannot read shows `Error`.

For a sum you only need the answer to, work it out yourself; use the Calculator when the user asks to see it done on the device.

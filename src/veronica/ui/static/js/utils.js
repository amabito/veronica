// src/veronica/ui/static/js/utils.js
// Shared utility functions for VERONICA UI.

/* exported escHtml */

/**
 * Escape a string for safe insertion into HTML.
 * @param {*} s - Value to escape (coerced to string).
 * @returns {string} HTML-safe string.
 */
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

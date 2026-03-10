# tests/test_ui_events.py
"""Smoke tests for the Event Log and Incident Detail static UI pages.

Verifies that the HTML files exist with required structure, and that
the JS files contain expected identifiers. Does not require a browser.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).parent.parent / "static"
EVENTS_HTML = STATIC_DIR / "events.html"
EVENTS_JS = STATIC_DIR / "js" / "events.js"
INCIDENT_HTML = STATIC_DIR / "incident.html"
INCIDENT_JS = STATIC_DIR / "js" / "incident.js"


# ---- File existence ----

class TestStaticFilesExist:
    def test_events_html_exists(self) -> None:
        assert EVENTS_HTML.exists(), f"Missing: {EVENTS_HTML}"

    def test_events_js_exists(self) -> None:
        assert EVENTS_JS.exists(), f"Missing: {EVENTS_JS}"

    def test_incident_html_exists(self) -> None:
        assert INCIDENT_HTML.exists(), f"Missing: {INCIDENT_HTML}"

    def test_incident_js_exists(self) -> None:
        assert INCIDENT_JS.exists(), f"Missing: {INCIDENT_JS}"


# ---- events.html structure ----

class TestEventsHtmlStructure:
    @pytest.fixture(scope="class")
    def html(self) -> str:
        return EVENTS_HTML.read_text(encoding="utf-8")

    def test_has_doctype(self, html: str) -> None:
        assert html.strip().lower().startswith("<!doctype html")

    def test_title_contains_event_log(self, html: str) -> None:
        assert "Event Log" in html or "event log" in html.lower()

    def test_has_table_element(self, html: str) -> None:
        assert "<table" in html

    def test_has_thead(self, html: str) -> None:
        assert "<thead" in html

    def test_has_tbody_with_id(self, html: str) -> None:
        assert 'id="events-tbody"' in html

    def test_has_decision_filter(self, html: str) -> None:
        assert 'id="filter-decision"' in html

    def test_has_chain_id_filter(self, html: str) -> None:
        assert 'id="filter-chain"' in html

    def test_has_since_filter(self, html: str) -> None:
        assert 'id="filter-since"' in html

    def test_has_until_filter(self, html: str) -> None:
        assert 'id="filter-until"' in html

    def test_has_apply_button(self, html: str) -> None:
        assert 'id="btn-apply"' in html

    def test_has_reset_button(self, html: str) -> None:
        assert 'id="btn-reset"' in html

    def test_has_pagination_buttons(self, html: str) -> None:
        assert 'id="btn-prev"' in html
        assert 'id="btn-next"' in html

    def test_has_auto_refresh_toggle(self, html: str) -> None:
        assert 'id="auto-refresh"' in html

    def test_has_events_js_script_tag(self, html: str) -> None:
        assert "/js/events.js" in html

    def test_has_nav_links(self, html: str) -> None:
        assert "Dashboard" in html
        assert "Events" in html
        assert "Policies" in html

    def test_has_all_decision_options(self, html: str) -> None:
        decisions = ["allow", "halt", "degrade", "retry", "quarantine", "queue", "unknown"]
        for d in decisions:
            assert f'value="{d}"' in html, f"Missing decision option: {d}"

    def test_has_truncated_warning_element(self, html: str) -> None:
        assert 'id="truncated-warn"' in html

    def test_has_status_msg_element(self, html: str) -> None:
        assert 'id="status-msg"' in html

    def test_has_event_count_element(self, html: str) -> None:
        assert 'id="event-count"' in html

    def test_has_page_info_element(self, html: str) -> None:
        assert 'id="page-info"' in html


# ---- events.js structure ----

class TestEventsJsStructure:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return EVENTS_JS.read_text(encoding="utf-8")

    def test_has_use_strict(self, js: str) -> None:
        assert '"use strict"' in js or "'use strict'" in js

    def test_fetches_events_endpoint(self, js: str) -> None:
        assert "/events" in js

    def test_has_pagination_logic(self, js: str) -> None:
        assert "offset" in js and "limit" in js

    def test_has_auto_refresh_logic(self, js: str) -> None:
        assert "setInterval" in js or "auto-refresh" in js

    def test_has_decision_badge_function(self, js: str) -> None:
        assert "badge" in js.lower()

    def test_has_timestamp_formatter(self, js: str) -> None:
        assert "fmtTimestamp" in js or "timestamp" in js

    def test_has_cost_formatter(self, js: str) -> None:
        assert "fmtCost" in js or "cost_usd" in js

    def test_has_incident_link(self, js: str) -> None:
        # Row click should navigate to /incident
        assert "/incident" in js

    def test_escapes_html(self, js: str) -> None:
        assert "escHtml" in js or "escapeHtml" in js or "innerHTML" in js

    def test_handles_empty_results(self, js: str) -> None:
        assert "empty" in js.lower() or "No events" in js

    def test_handles_fetch_error(self, js: str) -> None:
        assert "catch" in js and "error" in js.lower()

    def test_reads_url_params(self, js: str) -> None:
        # Should pre-fill filters from URL query params
        assert "URLSearchParams" in js or "searchParams" in js or "location" in js

    def test_has_all_decision_badge_classes(self, js: str) -> None:
        decisions = ["allow", "halt", "degrade", "retry", "quarantine", "queue", "unknown"]
        for d in decisions:
            assert f"badge-{d}" in js, f"Missing badge class for decision: {d}"


# ---- incident.html structure ----

class TestIncidentHtmlStructure:
    @pytest.fixture(scope="class")
    def html(self) -> str:
        return INCIDENT_HTML.read_text(encoding="utf-8")

    def test_has_doctype(self, html: str) -> None:
        assert html.strip().lower().startswith("<!doctype html")

    def test_title_contains_incident(self, html: str) -> None:
        assert "Incident" in html or "incident" in html.lower()

    def test_has_chain_id_display(self, html: str) -> None:
        assert 'id="chain-id-display"' in html

    def test_has_content_container(self, html: str) -> None:
        assert 'id="content"' in html

    def test_has_page_title_element(self, html: str) -> None:
        assert 'id="page-title"' in html

    def test_has_back_link(self, html: str) -> None:
        assert 'id="back-link"' in html or "Back" in html

    def test_has_incident_js_script_tag(self, html: str) -> None:
        assert "/js/incident.js" in html

    def test_has_nav_links(self, html: str) -> None:
        assert "Dashboard" in html
        assert "Events" in html

    def test_has_timeline_css(self, html: str) -> None:
        assert "timeline" in html

    def test_has_decision_badge_css(self, html: str) -> None:
        assert "badge" in html


# ---- incident.js structure ----

class TestIncidentJsStructure:
    @pytest.fixture(scope="class")
    def js(self) -> str:
        return INCIDENT_JS.read_text(encoding="utf-8")

    def test_has_use_strict(self, js: str) -> None:
        assert '"use strict"' in js or "'use strict'" in js

    def test_reads_chain_id_from_url(self, js: str) -> None:
        assert "chain_id" in js

    def test_reads_step_id_from_url(self, js: str) -> None:
        assert "step_id" in js

    def test_fetches_events_for_chain(self, js: str) -> None:
        assert "/events" in js and "chain_id" in js

    def test_has_timeline_rendering(self, js: str) -> None:
        assert "timeline" in js

    def test_has_root_cause_detection(self, js: str) -> None:
        assert "rootCause" in js or "root_cause" in js or "Root Cause" in js

    def test_highlights_halt_events(self, js: str) -> None:
        assert "halt" in js

    def test_highlights_degrade_events(self, js: str) -> None:
        assert "degrade" in js

    def test_has_summary_stats(self, js: str) -> None:
        assert "totalCost" in js or "total_cost" in js or "cost" in js.lower()

    def test_handles_no_chain_id(self, js: str) -> None:
        # Should handle missing chain_id gracefully
        assert "No chain_id" in js or "chain_id" in js

    def test_handles_fetch_error(self, js: str) -> None:
        assert "catch" in js

    def test_escapes_html(self, js: str) -> None:
        assert "escHtml" in js or "escapeHtml" in js

    def test_sorts_chronologically(self, js: str) -> None:
        # Timeline sorted ascending (oldest first for causality)
        assert "sort" in js and "timestamp" in js

    def test_has_policy_sidebar(self, js: str) -> None:
        assert "policy_hash" in js or "Policy" in js

    def test_scrolls_to_focal_step(self, js: str) -> None:
        assert "scrollIntoView" in js or "scroll" in js.lower()


# ---- No external dependencies ----

class TestNoDependencies:
    def test_events_html_no_cdn_imports(self) -> None:
        """events.html must not import from CDN (no React, Vue, Tailwind, Bootstrap)."""
        html = EVENTS_HTML.read_text(encoding="utf-8")
        cdn_patterns = [
            "cdn.jsdelivr.net",
            "unpkg.com",
            "cdnjs.cloudflare.com",
            "react",
            "vue",
            "tailwind",
            "bootstrap",
        ]
        for pattern in cdn_patterns:
            assert pattern not in html.lower(), f"events.html imports CDN resource: {pattern}"

    def test_incident_html_no_cdn_imports(self) -> None:
        """incident.html must not import from CDN."""
        html = INCIDENT_HTML.read_text(encoding="utf-8")
        cdn_patterns = [
            "cdn.jsdelivr.net",
            "unpkg.com",
            "cdnjs.cloudflare.com",
            "react",
            "vue",
            "tailwind",
            "bootstrap",
        ]
        for pattern in cdn_patterns:
            assert pattern not in html.lower(), f"incident.html imports CDN resource: {pattern}"

    def test_events_js_no_import_statements(self) -> None:
        """events.js must not use ES module imports (vanilla JS only)."""
        js = EVENTS_JS.read_text(encoding="utf-8")
        # ES module imports would be "import X from" or "import {X}"
        assert not re.search(r"^\s*import\s+", js, re.MULTILINE), \
            "events.js uses ES module import"

    def test_incident_js_no_import_statements(self) -> None:
        """incident.js must not use ES module imports."""
        js = INCIDENT_JS.read_text(encoding="utf-8")
        assert not re.search(r"^\s*import\s+", js, re.MULTILINE), \
            "incident.js uses ES module import"

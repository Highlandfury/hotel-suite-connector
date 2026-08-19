from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime, today

from hotel_suite_connector.night_audit.engine import run_read_only_preflight
from hotel_suite_connector.night_audit.orchestrator import prepare_night_audit_internal
from hotel_suite_connector.night_audit.scheduler_guard import get_effective_mode, guard_status


GUARD_VERSION = "0.5.0"
ALLOWED_ROLES = {
	"Front Desk",
	"Finance",
	"Kamra Agent",
	"Hotel Night Auditor",
	"System Manager",
}


def _require_run_role():
	if frappe.session.user == "Administrator":
		return
	if not ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to request night audit."), frappe.PermissionError)


def _record_event(
	property_name,
	business_date,
	mode,
	decision,
	message,
	control=None,
	batch=None,
	result=None,
):
	doc = frappe.new_doc("Hotel Night Audit Guard Event")
	doc.property = property_name
	doc.business_date = business_date
	doc.mode = mode
	doc.decision = decision
	doc.control = control
	doc.batch = batch
	doc.requested_by = frappe.session.user
	doc.requested_on = now_datetime()
	doc.guard_version = GUARD_VERSION
	doc.message = message
	doc.result_summary = json.dumps(result or {}, sort_keys=True, default=str, ensure_ascii=False, indent=2)
	doc.flags.from_night_audit_guard = True
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def run_night_audit(property: str, business_date: str | None = None):
	"""Controlled replacement for the public Kamra night-audit endpoint."""
	_require_run_role()
	if not property or not frappe.db.exists("Property", property):
		frappe.throw(_("Unknown hotel property: {0}").format(property or "(blank)"))
	business_date = getdate(business_date or today())
	mode = get_effective_mode()

	if mode == "Enforced":
		prepared = prepare_night_audit_internal(
			property,
			business_date,
			trigger="API Guard",
			requested_by=frappe.session.user,
		)
		message = (
			"Night audit was blocked by Hotel Suite Control. Orchestrator v0.5.0 prepared evidence "
			"but cannot execute until cashier close, approvals, backup evidence and ERP reconciliation are implemented."
		)
		event = _record_event(
			property,
			business_date,
			mode,
			"Blocked",
			message,
			control=prepared.get("preflight_control"),
			batch=prepared.get("batch"),
			result=prepared,
		)
		return {
			"blocked": True,
			"success": False,
			"mode": mode,
			"message": message,
			"batch": prepared.get("batch"),
			"control": prepared.get("preflight_control"),
			"orchestration_status": prepared.get("status"),
			"hard_blockers": prepared.get("hard_blockers"),
			"missing_gate_count": prepared.get("missing_gate_count"),
			"guard_event": event,
		}

	preflight = None
	if mode == "Shadow":
		preflight = run_read_only_preflight(property, business_date)

	from kamra.folio import run_night_audit as kamra_run_night_audit

	result = kamra_run_night_audit(property, str(business_date))
	decision = "Shadow Allowed" if mode == "Shadow" else "Allowed"
	message = (
		"Kamra night audit was allowed after a shadow preflight."
		if mode == "Shadow"
		else "Hotel Suite Control is Off; Kamra night audit was allowed unchanged."
	)
	_record_event(
		property,
		business_date,
		mode,
		decision,
		message,
		control=(preflight or {}).get("control"),
		result=result,
	)
	return result


@frappe.whitelist()
def get_guard_status():
	if frappe.session.user != "Administrator" and not {
		"System Manager", "Hotel Auditor", "Hotel General Manager", "Hotel Night Auditor"
	}.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to view night-audit guard status."), frappe.PermissionError)
	status = guard_status()
	status["guard_version"] = GUARD_VERSION
	return status

import frappe
from frappe import _

from hotel_suite_connector.night_audit.engine import run_read_only_preflight
from hotel_suite_connector.night_audit.orchestrator import (
	approve_night_audit as _approve_night_audit,
	get_orchestrator_status as _get_orchestrator_status,
	initialize_business_day as _initialize_business_day,
	prepare_night_audit as _prepare_night_audit,
	request_execution as _request_execution,
	verify_event_chain as _verify_event_chain,
)


ALLOWED_ROLES = {
	"System Manager",
	"Hotel General Manager",
	"Hotel Auditor",
	"Hotel Night Auditor",
}


def _require_night_audit_role():
	if frappe.session.user == "Administrator":
		return
	if not ALLOWED_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You do not have permission to run the night-audit preflight."), frappe.PermissionError)


@frappe.whitelist()
def run_preflight(property: str, business_date: str | None = None) -> dict:
	"""Create or reuse an immutable read-only PMS night-audit preflight."""
	_require_night_audit_role()
	return run_read_only_preflight(property, business_date)


@frappe.whitelist()
def initialize_business_day(property: str, business_date: str | None = None) -> dict:
	return _initialize_business_day(property, business_date)


@frappe.whitelist()
def prepare_night_audit(property: str, business_date: str | None = None) -> dict:
	return _prepare_night_audit(property, business_date)


@frappe.whitelist()
def approve_night_audit(batch: str, comment: str) -> dict:
	return _approve_night_audit(batch, comment)


@frappe.whitelist()
def request_execution(batch: str) -> dict:
	return _request_execution(batch)


@frappe.whitelist()
def verify_event_chain(batch: str) -> dict:
	return _verify_event_chain(batch)


@frappe.whitelist()
def get_orchestrator_status(property: str | None = None) -> dict:
	return _get_orchestrator_status(property)

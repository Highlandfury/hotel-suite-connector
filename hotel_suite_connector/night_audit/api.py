
import frappe
from frappe import _

from hotel_suite_connector.night_audit.engine import run_read_only_preflight


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

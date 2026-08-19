
import frappe
from frappe import _
from frappe.model.document import Document


class HotelNightAuditControl(Document):
	"""Immutable evidence generated only by the connector preflight engine."""

	def before_insert(self):
		if not self.flags.get("from_night_audit_engine"):
			frappe.throw(_("Night Audit Control records can only be created by the control engine."))
		self.execution_permitted = 0

	def validate(self):
		if not self.flags.get("from_night_audit_engine"):
			frappe.throw(_("Night Audit Control evidence is immutable."))
		if self.execution_permitted:
			frappe.throw(_("Night-audit execution is not available in engine v0.3.0."))

	def on_trash(self):
		frappe.throw(_("Night Audit Control evidence cannot be deleted."))

import frappe
from frappe import _
from frappe.model.document import Document


class HotelNightAuditStateEvent(Document):
	def before_insert(self):
		if not self.flags.get("from_night_audit_orchestrator"):
			frappe.throw(_("Night Audit State Events can only be created by the orchestrator."))
		if not self.event_hash:
			frappe.throw(_("Night Audit State Event hash is required."))

	def validate(self):
		if not self.flags.get("from_night_audit_orchestrator"):
			frappe.throw(_("Night Audit State Event evidence is immutable."))

	def on_trash(self):
		frappe.throw(_("Night Audit State Event evidence cannot be deleted."))

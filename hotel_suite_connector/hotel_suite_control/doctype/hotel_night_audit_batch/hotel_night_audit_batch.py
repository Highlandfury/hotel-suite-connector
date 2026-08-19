import frappe
from frappe import _
from frappe.model.document import Document


class HotelNightAuditBatch(Document):
	def before_insert(self):
		if not self.flags.get("from_night_audit_orchestrator"):
			frappe.throw(_("Hotel Night Audit Batch records can only be created by the night-audit orchestrator."))

	def validate(self):
		if not self.flags.get("from_night_audit_orchestrator"):
			frappe.throw(_("Hotel Night Audit Batch records can only be changed by the night-audit orchestrator."))

	def on_trash(self):
		frappe.throw(_("Hotel Night Audit Batch records cannot be deleted."))

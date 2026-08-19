
import frappe
from frappe import _
from frappe.model.document import Document


class HotelNightAuditGuardEvent(Document):
	def before_insert(self):
		if not self.flags.get("from_night_audit_guard"):
			frappe.throw(_("Night Audit Guard Events can only be created by the guard."))

	def validate(self):
		if not self.flags.get("from_night_audit_guard"):
			frappe.throw(_("Night Audit Guard Event evidence is immutable."))

	def on_trash(self):
		frappe.throw(_("Night Audit Guard Event evidence cannot be deleted."))

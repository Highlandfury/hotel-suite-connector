import frappe
from frappe import _
from frappe.model.document import Document


class HotelAuditChecklistTemplate(Document):
	def validate(self):
		if self.enabled and not self.items:
			frappe.throw(_("An enabled audit template requires at least one checklist item."))

		sequences = [row.sequence for row in self.items]
		if len(sequences) != len(set(sequences)):
			frappe.throw(_("Checklist item sequence numbers must be unique."))

		labels = [(row.check_item or "").strip().casefold() for row in self.items]
		if len(labels) != len(set(labels)):
			frappe.throw(_("Checklist items must be unique within a template."))

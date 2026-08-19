import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class HotelApprovalPolicy(Document):
	def validate(self):
		minimum = flt(self.minimum_amount)
		maximum = flt(self.maximum_amount)
		if maximum and maximum < minimum:
			frappe.throw(_("Maximum Amount cannot be less than Minimum Amount."))

		levels = [row.approval_level for row in self.approval_rules if row.approval_level]
		if len(levels) != len(set(levels)):
			frappe.throw(_("Approval levels must be unique within a policy."))

		if self.enabled and not self.approval_rules:
			frappe.throw(_("An enabled approval policy requires at least one approval rule."))

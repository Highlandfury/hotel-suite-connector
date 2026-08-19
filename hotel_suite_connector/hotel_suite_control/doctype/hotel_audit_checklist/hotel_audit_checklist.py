import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today


ALLOWED_STATUSES = {
	"Draft",
	"In Progress",
	"Pending Review",
	"Completed",
	"Rejected",
	"Cancelled",
}


class HotelAuditChecklist(Document):
	def before_insert(self):
		self.created_by = frappe.session.user
		self.created_on = now_datetime()
		self.business_date = self.business_date or today()
		self.status = "Draft"

	def validate(self):
		if self.status not in ALLOWED_STATUSES:
			frappe.throw(_("Invalid checklist status: {0}").format(self.status))

		if self.template and not self.items:
			self.load_template_items()

		if not self.items:
			frappe.throw(_("The audit checklist requires at least one item."))

		for row in self.items:
			if row.result == "Failed" and not (row.observation or "").strip():
				frappe.throw(_("An observation is required for failed item {0}.").format(row.check_item))
			if row.result in {"Passed", "Failed", "Not Applicable"}:
				row.completed_by = row.completed_by or frappe.session.user
				row.completed_on = row.completed_on or now_datetime()
			if row.result == "Pending":
				row.completed_by = None
				row.completed_on = None

		self.recalculate_totals()

	def load_template_items(self):
		template = frappe.get_doc("Hotel Audit Checklist Template", self.template)
		if not template.enabled:
			frappe.throw(_("The selected audit checklist template is disabled."))
		self.audit_type = template.audit_type
		self.department = self.department or template.department
		self.property_name = self.property_name or template.property_name
		for source in template.items:
			self.append(
				"items",
				{
					"sequence": source.sequence,
					"check_item": source.check_item,
					"category": source.category,
					"mandatory": source.mandatory,
					"requires_evidence": source.requires_evidence,
					"result": "Pending",
				},
			)

	def recalculate_totals(self):
		self.total_items = len(self.items)
		self.passed_items = sum(row.result == "Passed" for row in self.items)
		self.failed_items = sum(row.result == "Failed" for row in self.items)
		self.pending_items = sum(row.result == "Pending" for row in self.items)
		completed = self.total_items - self.pending_items
		self.completion_percentage = (completed / self.total_items * 100) if self.total_items else 0

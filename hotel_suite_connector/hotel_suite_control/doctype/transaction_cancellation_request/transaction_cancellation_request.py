import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, today


ALLOWED_STATUSES = {
	"Draft",
	"Pending Department Approval",
	"Pending Audit Approval",
	"Pending Finance Approval",
	"Pending GM Approval",
	"Approved",
	"Rejected",
	"Executed",
	"Failed",
}


class TransactionCancellationRequest(Document):
	def before_insert(self):
		self.requested_by = frappe.session.user
		self.requested_on = now_datetime()
		self.business_date = self.business_date or today()
		self.status = "Draft"

	def validate(self):
		if self.reference_doctype == self.doctype:
			frappe.throw(_("A cancellation request cannot reference another cancellation request."))

		if self.reference_doctype and self.reference_name:
			if not frappe.db.exists(self.reference_doctype, self.reference_name):
				frappe.throw(
					_("Referenced transaction {0} {1} does not exist.").format(
						self.reference_doctype, self.reference_name
					)
				)

		if not (self.reason or "").strip():
			frappe.throw(_("A cancellation reason is required."))

		if flt(self.transaction_amount) < 0:
			frappe.throw(_("Transaction Amount cannot be negative."))

		if self.status not in ALLOWED_STATUSES:
			frappe.throw(_("Invalid cancellation status: {0}").format(self.status))

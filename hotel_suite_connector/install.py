import frappe


CONTROL_ROLES = (
	"Hotel General Manager",
	"Hotel Auditor",
	"Hotel Department Head",
	"Hotel Storekeeper",
	"Hotel Receiving Officer",
	"Hotel Procurement Manager",
	"Hotel Night Auditor",
)


def before_install():
	ensure_roles()


def after_install():
	ensure_roles()
	ensure_default_checklist_templates()
	sync_night_audit_scheduler_guard()


def after_migrate():
	ensure_roles()
	ensure_default_checklist_templates()
	sync_night_audit_scheduler_guard()


def ensure_roles():
	"""Create stable application roles without changing existing assignments."""
	for role_name in CONTROL_ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": role_name,
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)



DEFAULT_CHECKLIST_TEMPLATES = (
	{
		"template_name": "Standard Hotel Night Audit",
		"audit_type": "Night Audit",
		"frequency": "Daily",
		"items": (
			("Front Office", "Resolve expected arrivals, cancellations and no-shows", True, False),
			("Front Office", "Confirm every in-house guest has an assigned room", True, False),
			("Revenue", "Post all room, restaurant and approved incidental charges", True, False),
			("Cash", "Reconcile cash, card, transfer and room-charge totals", True, True),
			("Finance", "Review unpaid departures and abnormal folio balances", True, True),
			("Revenue", "Verify approvals for voids, discounts, refunds and complimentary rooms", True, True),
			("Housekeeping", "Resolve front-office and housekeeping room-status differences", True, False),
			("Revenue", "Confirm restaurant and bar shifts are closed", True, False),
			("IT", "Confirm the scheduled backup completed successfully", True, True),
		),
	},
	{
		"template_name": "Store Stock Audit",
		"audit_type": "Stock Audit",
		"frequency": "Weekly",
		"items": (
			("Stock", "Freeze movements or define the count cut-off time", True, False),
			("Stock", "Count physical quantities by item and warehouse", True, True),
			("Stock", "Compare physical count with ERPNext stock balance", True, True),
			("Stock", "Investigate and document every material variance", True, True),
			("Stock", "Confirm approved stock reconciliation for accepted variances", True, True),
		),
	},
	{
		"template_name": "Store Receiving Audit",
		"audit_type": "Receiving Audit",
		"frequency": "Per Shift",
		"items": (
			("Purchasing", "Confirm an approved purchase order exists", True, False),
			("Stock", "Verify delivered quantity and condition", True, True),
			("Purchasing", "Match purchase order, receipt and supplier invoice", True, True),
			("Stock", "Record rejected, short or damaged items", True, True),
			("Stock", "Confirm accepted goods were posted to the correct warehouse", True, False),
		),
	},
	{
		"template_name": "Preventive Maintenance Audit",
		"audit_type": "PM Audit",
		"frequency": "Weekly",
		"items": (
			("Maintenance", "Review preventive maintenance tasks due in the period", True, False),
			("Maintenance", "Confirm completed work has technician notes", True, False),
			("Maintenance", "Confirm parts issued are linked to the maintenance work", True, True),
			("Maintenance", "Escalate overdue or safety-critical maintenance", True, True),
		),
	},
)


def ensure_default_checklist_templates():
	"""Seed standard templates once; never overwrite a hotel's edited template."""
	if not frappe.db.exists("DocType", "Hotel Audit Checklist Template"):
		return

	for definition in DEFAULT_CHECKLIST_TEMPLATES:
		if frappe.db.exists("Hotel Audit Checklist Template", definition["template_name"]):
			continue
		doc = frappe.new_doc("Hotel Audit Checklist Template")
		doc.template_name = definition["template_name"]
		doc.audit_type = definition["audit_type"]
		doc.frequency = definition["frequency"]
		doc.enabled = 1
		for sequence, item in enumerate(definition["items"], start=1):
			category, check_item, mandatory, requires_evidence = item
			doc.append(
				"items",
				{
					"sequence": sequence,
					"category": category,
					"check_item": check_item,
					"mandatory": mandatory,
					"requires_evidence": requires_evidence,
				},
			)
		doc.insert(ignore_permissions=True)

def sync_night_audit_scheduler_guard():
	"""Apply the selected scheduler state after DocTypes and jobs are synchronized."""
	from hotel_suite_connector.night_audit.scheduler_guard import sync_scheduler_guard

	sync_scheduler_guard()

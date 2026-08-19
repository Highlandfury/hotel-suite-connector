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


def after_migrate():
	ensure_roles()


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

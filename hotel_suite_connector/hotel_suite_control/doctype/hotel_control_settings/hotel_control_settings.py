
from frappe.model.document import Document


class HotelControlSettings(Document):
	def on_update(self):
		from hotel_suite_connector.night_audit.scheduler_guard import sync_scheduler_guard

		sync_scheduler_guard()

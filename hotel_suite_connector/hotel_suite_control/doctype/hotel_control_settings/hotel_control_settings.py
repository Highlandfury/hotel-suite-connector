from frappe.model.document import Document
from frappe.utils import cint


class HotelControlSettings(Document):
	def on_update(self):
		from hotel_suite_connector.night_audit.scheduler import sync_orchestrator_scheduler
		from hotel_suite_connector.night_audit.scheduler_guard import sync_scheduler_guard

		guard = sync_scheduler_guard()
		orchestrator = sync_orchestrator_scheduler()

		# The sync functions persist with db.set_single_value during on_update.
		# Mirror the values into this in-memory document so API/UI save responses
		# do not display the previous state until a manual refresh.
		self.kamra_scheduler_guard_active = cint(guard.get("guard_active"))
		self.kamra_scheduler_original_stopped = cint(guard.get("original_stopped"))
		self.kamra_scheduler_job = guard.get("job")
		self.night_audit_orchestrator_version = orchestrator.get("orchestrator_version")
		self.connector_scheduler_job = orchestrator.get("job")

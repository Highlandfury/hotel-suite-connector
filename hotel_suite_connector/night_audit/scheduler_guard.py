
from __future__ import annotations

import frappe
from frappe.utils import cint, now_datetime


KAMRA_SCHEDULER_METHOD = "kamra.folio.nightly_audit_all_properties"
VALID_MODES = {"Off", "Shadow", "Enforced"}


def get_effective_mode(settings=None) -> str:
	"""Return Off unless both the master switch and a valid mode are enabled."""
	if not frappe.db.exists("DocType", "Hotel Control Settings"):
		return "Off"
	settings = settings or frappe.get_single("Hotel Control Settings")
	if not cint(settings.get("enable_control_module")):
		return "Off"
	mode = settings.get("night_audit_mode") or "Off"
	return mode if mode in VALID_MODES else "Off"


def get_scheduler_job() -> dict | None:
	row = frappe.db.get_value(
		"Scheduled Job Type",
		{"method": KAMRA_SCHEDULER_METHOD},
		["name", "method", "stopped", "frequency", "cron_format"],
		as_dict=True,
	)
	return dict(row) if row else None


def _set_single(fieldname: str, value) -> None:
	frappe.db.set_single_value("Hotel Control Settings", fieldname, value)


def sync_scheduler_guard() -> dict:
	"""Stop or restore Kamra's scheduler according to the effective mode.

	The prior stopped state is captured only when the connector first takes
	control. Disabling enforcement restores exactly that prior state.
	"""
	if not frappe.db.exists("DocType", "Hotel Control Settings"):
		return {"mode": "Off", "job_found": False, "guard_active": False}
	settings = frappe.get_single("Hotel Control Settings")
	selected_mode = settings.get("night_audit_mode")
	if selected_mode not in VALID_MODES:
		# Frappe does not backfill defaults into an existing Single record when
		# a field is added. Persist the safest mode during install/migrate sync.
		_set_single("night_audit_mode", "Off")
		settings.night_audit_mode = "Off"
	mode = get_effective_mode(settings)
	job = get_scheduler_job()
	active = cint(settings.get("kamra_scheduler_guard_active"))
	original_stopped = cint(settings.get("kamra_scheduler_original_stopped"))
	scheduler_stopped = cint(job.get("stopped")) if job else 0

	if not job:
		_set_single("kamra_scheduler_job", None)
		_set_single("night_audit_guard_last_synced_on", now_datetime())
		frappe.log_error(
			message=f"Scheduled Job Type not found for {KAMRA_SCHEDULER_METHOD}",
			title="Hotel Night Audit Guard: Kamra scheduler missing",
		)
		return {"mode": mode, "job_found": False, "guard_active": bool(active)}

	if mode == "Enforced":
		if not active:
			original_stopped = cint(job.get("stopped"))
			_set_single("kamra_scheduler_original_stopped", original_stopped)
		if not cint(job.get("stopped")):
			frappe.db.set_value(
				"Scheduled Job Type", job["name"], "stopped", 1, update_modified=False
			)
		scheduler_stopped = 1
		_set_single("kamra_scheduler_guard_active", 1)
		active = 1
	else:
		if active:
			frappe.db.set_value(
				"Scheduled Job Type",
				job["name"],
				"stopped",
				original_stopped,
				update_modified=False,
			)
			scheduler_stopped = original_stopped
			_set_single("kamra_scheduler_guard_active", 0)
			active = 0

	_set_single("kamra_scheduler_job", job["name"])
	_set_single("night_audit_guard_last_synced_on", now_datetime())
	return {
		"mode": mode,
		"job_found": True,
		"job": job["name"],
		"scheduler_method": KAMRA_SCHEDULER_METHOD,
		"scheduler_stopped": bool(scheduler_stopped),
		"guard_active": bool(active),
		"original_stopped": bool(original_stopped),
	}


def guard_status() -> dict:
	settings = frappe.get_single("Hotel Control Settings")
	job = get_scheduler_job()
	return {
		"master_enabled": bool(cint(settings.get("enable_control_module"))),
		"selected_mode": settings.get("night_audit_mode") or "Off",
		"effective_mode": get_effective_mode(settings),
		"guard_active": bool(cint(settings.get("kamra_scheduler_guard_active"))),
		"original_stopped": bool(cint(settings.get("kamra_scheduler_original_stopped"))),
		"scheduler_job": job,
	}

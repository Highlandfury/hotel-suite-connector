from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, getdate, now_datetime

from hotel_suite_connector.night_audit.scheduler_guard import get_effective_mode


ORCHESTRATOR_VERSION = "0.5.0"
CONNECTOR_SCHEDULER_METHOD = "hotel_suite_connector.night_audit.scheduler.tick"


def _set_single(fieldname: str, value) -> None:
	frappe.db.set_single_value("Hotel Control Settings", fieldname, value)


def get_connector_scheduler_job() -> dict | None:
	row = frappe.db.get_value(
		"Scheduled Job Type",
		{"method": CONNECTOR_SCHEDULER_METHOD},
		["name", "method", "stopped", "frequency", "cron_format", "last_execution"],
		as_dict=True,
	)
	return dict(row) if row else None


def sync_orchestrator_scheduler() -> dict:
	"""Persist safe defaults and discover the hook-managed scheduler record."""
	if not frappe.db.exists("DocType", "Hotel Control Settings"):
		return {"job_found": False, "orchestrator_version": ORCHESTRATOR_VERSION}

	defaults = {
		"enable_controlled_night_audit_scheduler": 0,
		"require_separate_night_audit_approver": 1,
		"night_audit_preflight_time": "02:45:00",
		"night_audit_execution_time": "03:00:00",
		"night_audit_orchestrator_version": ORCHESTRATOR_VERSION,
	}
	for fieldname, value in defaults.items():
		current = frappe.db.get_single_value("Hotel Control Settings", fieldname)
		if current in (None, ""):
			_set_single(fieldname, value)
	_set_single("night_audit_orchestrator_version", ORCHESTRATOR_VERSION)

	job = get_connector_scheduler_job()
	_set_single("connector_scheduler_job", job["name"] if job else None)
	_set_single("connector_scheduler_last_synced_on", now_datetime())
	if not job:
		frappe.log_error(
			message=f"Scheduled Job Type not found for {CONNECTOR_SCHEDULER_METHOD}",
			title="Hotel Night Audit Orchestrator: scheduler missing",
		)
	return {
		"job_found": bool(job),
		"job": job["name"] if job else None,
		"scheduler_stopped": bool(cint(job.get("stopped"))) if job else None,
		"orchestrator_version": ORCHESTRATOR_VERSION,
	}


def _parse_time(value, default="02:45:00") -> time:
	text = str(value or default).split(".")[0]
	parts = [int(part) for part in text.split(":")[:3]]
	while len(parts) < 3:
		parts.append(0)
	return time(*parts)


def tick() -> dict:
	"""Five-minute scheduler gate; prepare at most one batch per business day."""
	if not frappe.db.exists("DocType", "Hotel Control Settings"):
		return {"active": False, "reason": "settings_missing"}
	settings = frappe.get_single("Hotel Control Settings")
	if not cint(settings.get("enable_control_module")):
		return {"active": False, "reason": "control_module_disabled"}
	if not cint(settings.get("enable_controlled_night_audit_scheduler")):
		return {"active": False, "reason": "controlled_scheduler_disabled"}

	mode = get_effective_mode(settings)
	if mode not in {"Shadow", "Enforced"}:
		return {"active": False, "reason": "mode_not_controlled", "mode": mode}

	timezone_name = settings.get("timezone") or "Africa/Lagos"
	try:
		local_now = datetime.now(ZoneInfo(timezone_name))
	except Exception:
		frappe.log_error(
			message=f"Invalid Hotel Control Settings timezone: {timezone_name}",
			title="Hotel Night Audit Orchestrator: invalid timezone",
		)
		return {"active": False, "reason": "invalid_timezone", "timezone": timezone_name}

	preflight_time = _parse_time(settings.get("night_audit_preflight_time"))
	_set_single("connector_scheduler_last_tick", now_datetime())
	if local_now.time().replace(tzinfo=None) < preflight_time:
		return {
			"active": True,
			"reason": "before_preflight_time",
			"local_time": str(local_now),
			"preflight_time": str(preflight_time),
		}

	from hotel_suite_connector.night_audit.orchestrator import prepare_night_audit_internal

	results = []
	business_days = frappe.get_all(
		"Hotel Business Day",
		filters={"status": "Open", "business_date": ("<=", getdate(local_now.date()))},
		fields=["name", "property", "business_date"],
		order_by="business_date asc, property asc",
		limit_page_length=1000,
	)
	for row in business_days:
		try:
			results.append(
				prepare_night_audit_internal(
					row.property,
					row.business_date,
					trigger="Scheduler",
					requested_by="Administrator",
				)
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Controlled night-audit preparation failed: {row.property}",
			)
			results.append({"property": row.property, "success": False, "reason": "exception"})
	return {
		"active": True,
		"mode": mode,
		"business_days_considered": len(business_days),
		"results": results,
	}


def scheduler_status() -> dict:
	settings = frappe.get_single("Hotel Control Settings")
	return {
		"orchestrator_version": ORCHESTRATOR_VERSION,
		"master_enabled": bool(cint(settings.get("enable_control_module"))),
		"controlled_scheduler_enabled": bool(
			cint(settings.get("enable_controlled_night_audit_scheduler"))
		),
		"effective_mode": get_effective_mode(settings),
		"preflight_time": str(settings.get("night_audit_preflight_time") or "02:45:00"),
		"execution_time": str(settings.get("night_audit_execution_time") or "03:00:00"),
		"scheduler_job": get_connector_scheduler_job(),
	}

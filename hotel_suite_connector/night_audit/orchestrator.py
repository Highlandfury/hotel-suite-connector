from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, today

from hotel_suite_connector.night_audit.engine import run_read_only_preflight


ORCHESTRATOR_VERSION = "0.5.0"
PREPARE_ROLES = {"System Manager", "Hotel General Manager", "Hotel Auditor", "Hotel Night Auditor"}
APPROVER_ROLES = {"System Manager", "Hotel General Manager", "Hotel Auditor"}
VIEW_ROLES = PREPARE_ROLES | {"Hotel Department Head"}

ALLOWED_TRANSITIONS = {
	"Draft": {"Preflight Running", "Cancelled"},
	"Preflight Running": {"Blocked", "Ready for Approval", "Failed"},
	"Blocked": {"Preflight Running", "Cancelled"},
	"Ready for Approval": {"Preflight Running", "Approved", "Cancelled"},
	"Approved": {"Preflight Running", "Queued", "Cancelled"},
	"Queued": {"Executing", "Failed"},
	"Executing": {"Completed", "Failed", "Recovery Required"},
	"Failed": {"Preflight Running", "Recovery Required", "Cancelled"},
	"Recovery Required": {"Preflight Running", "Cancelled"},
	"Completed": set(),
	"Cancelled": set(),
}

BUSINESS_DAY_STATUS = {
	"Draft": "Open",
	"Preflight Running": "Preflight Running",
	"Blocked": "Blocked",
	"Ready for Approval": "Ready for Approval",
	"Approved": "Approved",
	"Queued": "Approved",
	"Executing": "Executing",
	"Completed": "Completed",
	"Failed": "Failed",
	"Recovery Required": "Recovery Required",
	"Cancelled": "Open",
}


def _actor() -> str:
	return getattr(frappe.session, "user", None) or "Administrator"


def _require_roles(roles: set[str], message: str) -> None:
	if _actor() == "Administrator":
		return
	if not roles.intersection(frappe.get_roles()):
		frappe.throw(_(message), frappe.PermissionError)


def _validate_property(property_name: str) -> None:
	if not property_name or not frappe.db.exists("Property", property_name):
		frappe.throw(_("Unknown hotel property: {0}").format(property_name or "(blank)"))
	if cint(frappe.db.get_value("Property", property_name, "disabled")):
		frappe.throw(_("Hotel property {0} is disabled.").format(property_name))


def _lock_name(property_name: str, business_date) -> str:
	site = getattr(frappe.local, "site", "site")
	digest = hashlib.sha256(f"{site}|{property_name}|{business_date}".encode()).hexdigest()
	return f"hnab:{digest[:50]}"


@contextmanager
def operation_lock(property_name: str, business_date, timeout=10):
	name = _lock_name(property_name, business_date)
	rows = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (name, timeout))
	acquired = rows and rows[0][0] == 1
	if not acquired:
		frappe.throw(_("Another controlled night-audit operation is already running."))
	try:
		yield
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (name,))


def _canonical(value) -> str:
	return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))


def compute_event_hash(
	batch: str,
	sequence: int,
	from_status: str | None,
	to_status: str,
	event_type: str,
	actor: str,
	occurred_on,
	trigger: str,
	control: str | None,
	message: str,
	details_json: str,
	previous_hash: str | None,
) -> str:
	payload = {
		"batch": batch,
		"sequence": cint(sequence),
		"from_status": from_status or "",
		"to_status": to_status,
		"event_type": event_type,
		"actor": actor,
		"occurred_on": str(occurred_on),
		"trigger": trigger,
		"control": control or "",
		"message": message or "",
		"details_json": details_json or "{}",
		"previous_hash": previous_hash or "",
	}
	return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _save_controlled(doc, *, insert=False):
	doc.flags.from_night_audit_orchestrator = True
	if insert:
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc


def _record_event(
	batch,
	from_status,
	to_status,
	event_type,
	message,
	*,
	trigger=None,
	control=None,
	details=None,
	actor=None,
):
	previous = frappe.get_all(
		"Hotel Night Audit State Event",
		filters={"batch": batch.name},
		fields=["sequence", "event_hash"],
		order_by="sequence desc",
		limit_page_length=1,
	)
	sequence = cint(previous[0].sequence) + 1 if previous else 1
	previous_hash = previous[0].event_hash if previous else None
	occurred_on = now_datetime()
	actor = actor or _actor()
	trigger = trigger or batch.trigger or "Manual"
	details_json = json.dumps(details or {}, sort_keys=True, default=str, ensure_ascii=False, indent=2)
	event_hash = compute_event_hash(
		batch.name,
		sequence,
		from_status,
		to_status,
		event_type,
		actor,
		occurred_on,
		trigger,
		control,
		message,
		details_json,
		previous_hash,
	)
	event = frappe.new_doc("Hotel Night Audit State Event")
	event.batch = batch.name
	event.property = batch.property
	event.business_date = batch.business_date
	event.sequence = sequence
	event.from_status = from_status or ""
	event.to_status = to_status
	event.event_type = event_type
	event.actor = actor
	event.occurred_on = occurred_on
	event.trigger = trigger
	event.control = control
	event.message = message
	event.details_json = details_json
	event.previous_hash = previous_hash
	event.event_hash = event_hash
	_save_controlled(event, insert=True)
	return event


def _business_day_summary(doc) -> dict:
	return {
		"name": doc.name,
		"property": doc.property,
		"business_date": str(doc.business_date),
		"status": doc.status,
		"active_batch": doc.active_batch,
		"next_business_date": str(doc.next_business_date) if doc.next_business_date else None,
		"orchestrator_version": doc.orchestrator_version,
	}


def _batch_summary(doc) -> dict:
	return {
		"batch": doc.name,
		"property": doc.property,
		"business_date": str(doc.business_date),
		"status": doc.status,
		"stage": doc.stage,
		"trigger": doc.trigger,
		"attempt_count": cint(doc.attempt_count),
		"preflight_control": doc.preflight_control,
		"preflight_status": doc.preflight_status,
		"hard_blockers": cint(doc.hard_blockers),
		"missing_gate_count": cint(doc.missing_gate_count),
		"execution_permitted": False,
		"next_action": doc.next_action,
		"orchestrator_version": doc.orchestrator_version,
	}


def _get_business_day(property_name: str):
	name = frappe.db.get_value("Hotel Business Day", {"property": property_name}, "name")
	return frappe.get_doc("Hotel Business Day", name) if name else None


def initialize_business_day(property_name: str, business_date=None) -> dict:
	_require_roles({"System Manager"}, "Only System Manager can initialize a hotel business date.")
	_validate_property(property_name)
	business_date = getdate(business_date or today())
	with operation_lock(property_name, business_date):
		existing = _get_business_day(property_name)
		if existing:
			if getdate(existing.business_date) != business_date:
				frappe.throw(
					_(
						"Business date is already initialized as {0}. Controlled rollover is not available in v0.5.0."
					).format(existing.business_date)
				)
			result = _business_day_summary(existing)
			result["created"] = False
			return result

		doc = frappe.new_doc("Hotel Business Day")
		doc.property = property_name
		doc.business_date = business_date
		doc.status = "Open"
		doc.next_business_date = add_days(business_date, 1)
		doc.opened_by = _actor()
		doc.opened_on = now_datetime()
		doc.last_transition_on = doc.opened_on
		doc.orchestrator_version = ORCHESTRATOR_VERSION
		_save_controlled(doc, insert=True)
		result = _business_day_summary(doc)
		result["created"] = True
		return result


def _idempotency_key(property_name: str, business_date) -> str:
	site = getattr(frappe.local, "site", "site")
	return hashlib.sha256(f"{site}|{property_name}|{business_date}".encode()).hexdigest()


def _get_or_create_batch(business_day, trigger: str, requested_by: str):
	key = _idempotency_key(business_day.property, business_day.business_date)
	name = frappe.db.get_value("Hotel Night Audit Batch", {"idempotency_key": key}, "name")
	if name:
		return frappe.get_doc("Hotel Night Audit Batch", name), False

	batch = frappe.new_doc("Hotel Night Audit Batch")
	batch.property = business_day.property
	batch.business_date = business_day.business_date
	batch.business_day = business_day.name
	batch.idempotency_key = key
	batch.status = "Draft"
	batch.stage = "Created"
	batch.trigger = trigger
	batch.requested_by = requested_by
	batch.requested_on = now_datetime()
	batch.orchestrator_version = ORCHESTRATOR_VERSION
	batch.next_action = "Run evidence-backed preflight."
	_save_controlled(batch, insert=True)

	business_day.active_batch = batch.name
	business_day.last_transition_on = now_datetime()
	_save_controlled(business_day)
	_record_event(
		batch,
		None,
		"Draft",
		"Created",
		"Controlled night-audit batch created.",
		trigger=trigger,
		actor=requested_by,
	)
	return batch, True


def _sync_business_day(batch) -> None:
	business_day = frappe.get_doc("Hotel Business Day", batch.business_day)
	business_day.status = BUSINESS_DAY_STATUS.get(batch.status, business_day.status)
	business_day.active_batch = batch.name
	business_day.last_transition_on = now_datetime()
	_save_controlled(business_day)


def _transition(
	batch,
	target_status: str,
	event_type: str,
	message: str,
	*,
	updates=None,
	details=None,
	trigger=None,
	actor=None,
):
	previous_status = batch.status
	if target_status not in ALLOWED_TRANSITIONS.get(previous_status, set()):
		frappe.throw(
			_("Invalid night-audit transition from {0} to {1}.").format(
				previous_status, target_status
			)
		)
	for fieldname, value in (updates or {}).items():
		setattr(batch, fieldname, value)
	batch.status = target_status
	batch.stage = event_type
	batch.last_transition_on = now_datetime()
	batch.orchestrator_version = ORCHESTRATOR_VERSION
	_save_controlled(batch)
	_sync_business_day(batch)
	_record_event(
		batch,
		previous_status,
		target_status,
		event_type,
		message,
		trigger=trigger,
		control=batch.preflight_control,
		details=details,
		actor=actor,
	)
	return batch


def prepare_night_audit_internal(
	property_name: str,
	business_date=None,
	*,
	trigger="Manual",
	requested_by=None,
) -> dict:
	_validate_property(property_name)
	requested_by = requested_by or _actor()
	business_date = getdate(business_date or today())
	with operation_lock(property_name, business_date):
		business_day = _get_business_day(property_name)
		if not business_day:
			return {
				"success": False,
				"blocked": True,
				"property": property_name,
				"business_date": str(business_date),
				"reason": "business_day_not_initialized",
				"message": "A System Manager must initialize Hotel Business Day before controlled preparation.",
			}
		if getdate(business_day.business_date) != business_date:
			return {
				"success": False,
				"blocked": True,
				"property": property_name,
				"business_date": str(business_date),
				"current_business_date": str(business_day.business_date),
				"reason": "business_date_mismatch",
				"message": "Requested date does not match the property's controlled business date.",
			}

		batch, created = _get_or_create_batch(business_day, trigger, requested_by)
		if batch.status in {"Executing", "Completed", "Recovery Required", "Cancelled"}:
			result = _batch_summary(batch)
			result.update({"success": False, "blocked": True, "created": created})
			return result
		if batch.status == "Preflight Running":
			result = _batch_summary(batch)
			result.update(
				{
					"success": False,
					"blocked": True,
					"created": created,
					"reason": "preflight_already_running",
				}
			)
			return result

		attempt = cint(batch.attempt_count) + 1
		_transition(
			batch,
			"Preflight Running",
			"Preflight Started",
			f"Evidence-backed preflight attempt {attempt} started.",
			updates={
				"attempt_count": attempt,
				"last_attempt_by": requested_by,
				"last_attempt_on": now_datetime(),
				"trigger": trigger,
				"error_summary": None,
				"error_trace": None,
				"next_action": "Wait for preflight completion.",
			},
			trigger=trigger,
			actor=requested_by,
			details={"attempt": attempt},
		)
		try:
			preflight = run_read_only_preflight(property_name, business_date)
			control = frappe.get_doc("Hotel Night Audit Control", preflight["control"])
			missing_gates = cint(control.skipped_checks) + cint(control.error_checks)
			ready = bool(
				preflight.get("execution_permitted")
				and preflight.get("status") == "Passed"
				and not cint(preflight.get("hard_blockers"))
				and not missing_gates
			)
			if ready:
				target = "Ready for Approval"
				event_type = "Ready"
				message = "All implemented evidence gates passed; independent approval is required."
				next_action = "A different authorized approver must review and approve the batch."
			else:
				target = "Blocked"
				event_type = "Blocked"
				if cint(preflight.get("hard_blockers")):
					next_action = "Resolve every hard blocker and run preflight again."
				elif missing_gates:
					next_action = "Provide the missing cashier, ERP, backup and approval evidence gates."
				else:
					next_action = "Controlled execution remains disabled in orchestrator v0.5.0."
				message = "Night-audit batch is blocked; no Kamra execution was attempted."
			_transition(
				batch,
				target,
				event_type,
				message,
				updates={
					"preflight_control": control.name,
					"preflight_status": control.status,
					"hard_blockers": cint(control.hard_blockers),
					"missing_gate_count": missing_gates,
					"last_preflight_on": control.completed_on,
					"next_action": next_action,
					"result_json": json.dumps(
						preflight, sort_keys=True, default=str, ensure_ascii=False, indent=2
					),
				},
				trigger=trigger,
				actor=requested_by,
				details=preflight,
			)
			result = _batch_summary(batch)
			result.update({"success": True, "blocked": not ready, "created": created})
			return result
		except Exception:
			trace = frappe.get_traceback()
			_transition(
				batch,
				"Failed",
				"Failed",
				"Preflight failed unexpectedly; no Kamra execution was attempted.",
				updates={
					"error_summary": "Night-audit preflight raised an exception.",
					"error_trace": trace,
					"next_action": "Review Error Log, correct the failure and retry preflight.",
				},
				trigger=trigger,
				actor=requested_by,
			)
			frappe.log_error(trace, f"Controlled night-audit preflight failed: {batch.name}")
			result = _batch_summary(batch)
			result.update({"success": False, "blocked": True, "created": created})
			return result


def prepare_night_audit(property_name: str, business_date=None) -> dict:
	_require_roles(PREPARE_ROLES, "You do not have permission to prepare night audit.")
	return prepare_night_audit_internal(property_name, business_date)


def approve_night_audit(batch_name: str, comment: str) -> dict:
	_require_roles(APPROVER_ROLES, "You do not have permission to approve night audit.")
	if not str(comment or "").strip():
		frappe.throw(_("Approval comment is required."))
	batch = frappe.get_doc("Hotel Night Audit Batch", batch_name)
	with operation_lock(batch.property, batch.business_date):
		batch.reload()
		if batch.status != "Ready for Approval":
			frappe.throw(_("Only a Ready for Approval batch can be approved."))
		settings = frappe.get_single("Hotel Control Settings")
		if cint(settings.get("require_separate_night_audit_approver")) and batch.requested_by == _actor():
			frappe.throw(_("The night-audit requester cannot approve the same batch."))
		_transition(
			batch,
			"Approved",
			"Approved",
			"Night-audit batch independently approved.",
			updates={
				"approved_by": _actor(),
				"approved_on": now_datetime(),
				"approval_comment": comment.strip(),
				"next_action": "Controlled execution remains disabled in v0.5.0.",
			},
			details={"comment": comment.strip()},
		)
		return _batch_summary(batch)


def request_execution(batch_name: str) -> dict:
	_require_roles(APPROVER_ROLES, "You do not have permission to request execution.")
	batch = frappe.get_doc("Hotel Night Audit Batch", batch_name)
	with operation_lock(batch.property, batch.business_date):
		batch.reload()
		message = (
			"Controlled execution is intentionally disabled in orchestrator v0.5.0 until cashier close, "
			"approval, backup and ERP reconciliation gates are implemented."
		)
		event = _record_event(
			batch,
			batch.status,
			batch.status,
			"Execution Blocked",
			message,
			control=batch.preflight_control,
			details={"orchestrator_version": ORCHESTRATOR_VERSION},
		)
		return {
			"success": False,
			"blocked": True,
			"batch": batch.name,
			"status": batch.status,
			"event": event.name,
			"message": message,
		}


def verify_event_chain(batch_name: str) -> dict:
	_require_roles(VIEW_ROLES, "You do not have permission to verify night-audit evidence.")
	events = frappe.get_all(
		"Hotel Night Audit State Event",
		filters={"batch": batch_name},
		fields=[
			"name", "sequence", "from_status", "to_status", "event_type", "actor",
			"occurred_on", "trigger", "control", "message", "details_json",
			"previous_hash", "event_hash",
		],
		order_by="sequence asc",
		limit_page_length=10000,
	)
	previous_hash = None
	issues = []
	for expected_sequence, event in enumerate(events, start=1):
		computed = compute_event_hash(
			batch_name,
			event.sequence,
			event.from_status,
			event.to_status,
			event.event_type,
			event.actor,
			event.occurred_on,
			event.trigger,
			event.control,
			event.message,
			event.details_json,
			event.previous_hash,
		)
		if cint(event.sequence) != expected_sequence:
			issues.append({"event": event.name, "issue": "sequence_gap"})
		if (event.previous_hash or None) != previous_hash:
			issues.append({"event": event.name, "issue": "previous_hash_mismatch"})
		if event.event_hash != computed:
			issues.append({"event": event.name, "issue": "event_hash_mismatch"})
		previous_hash = event.event_hash
	return {
		"batch": batch_name,
		"valid": not issues and bool(events),
		"event_count": len(events),
		"head_hash": previous_hash,
		"issues": issues,
	}


def get_orchestrator_status(property_name: str | None = None) -> dict:
	_require_roles(VIEW_ROLES, "You do not have permission to view night-audit orchestration.")
	from hotel_suite_connector.night_audit.scheduler import scheduler_status

	result = {"scheduler": scheduler_status()}
	if property_name:
		business_day = _get_business_day(property_name)
		result["business_day"] = _business_day_summary(business_day) if business_day else None
		batch = None
		if business_day and business_day.active_batch:
			batch = frappe.get_doc("Hotel Night Audit Batch", business_day.active_batch)
		result["batch"] = _batch_summary(batch) if batch else None
		if batch:
			result["event_chain"] = verify_event_chain(batch.name)
	return result

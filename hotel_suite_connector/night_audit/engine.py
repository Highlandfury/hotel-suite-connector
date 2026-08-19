
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import time
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime, today


ENGINE_VERSION = "0.3.0"
EXPECTED_KAMRA_VERSION = "2.5.0"
MAX_EVIDENCE_ROWS = 50


REQUIRED_SCHEMA = {
	"Property": {
		"country", "timezone", "currency", "no_show_charge", "disabled", "require_cashier_pin",
	},
	"Reservation": {
		"property", "guest", "guest_name", "status", "booking_type", "company", "source",
		"channel", "ota_ref", "room", "room_type", "meal_plan", "check_in_date",
		"check_out_date", "amount_after_tax", "advance_paid", "discount_amount",
		"is_pay_at_hotel",
	},
	"Folio": {
		"property", "reservation", "guest", "status", "folio_type", "charges", "payments",
		"charges_total", "tax_total", "grand_total", "payments_total", "balance",
		"invoice_number", "opened_on", "closed_on",
	},
	"Folio Charge": {
		"posting_date", "charge_type", "amount", "gst_rate", "gst_amount",
		"total", "reservation", "description", "auto_posted",
	},
	"Folio Payment": {
		"posting_date", "payment_kind", "mode", "amount", "currency", "exchange_rate", "reference",
	},
	"POS Order": {
		"property", "outlet", "status", "room", "reservation", "order_total",
		"posted_to_folio", "paid", "payment_mode", "discount_amount",
		"discount_reason", "order_type", "nc", "nc_authorized_by",
	},
	"Room": {"property", "room_number", "room_type", "housekeeping_status", "occupancy_status"},
	"Night Audit Run": {
		"property", "business_date", "status", "room_charges_posted", "amount_posted",
	},
}


def _json(value) -> str:
	return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False, separators=(",", ":"))


def _rows(doctype: str, *, filters=None, fields=None, order_by="name asc") -> list[dict]:
	return [
		dict(row)
		for row in frappe.get_all(
			doctype,
			filters=filters or {},
			fields=fields or ["name"],
			order_by=order_by,
			limit_page_length=10000,
		)
	]


def _evidence(rows=None, **extra) -> str:
	rows = list(rows or [])
	payload = {
		"records": rows[:MAX_EVIDENCE_ROWS],
		"record_count": len(rows),
		"truncated": len(rows) > MAX_EVIDENCE_ROWS,
	}
	payload.update(extra)
	return json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False, indent=2)


def _check(code, category, title, severity, result, message, rows=None, amount=0, **evidence):
	rows = list(rows or [])
	return {
		"check_code": code,
		"category": category,
		"title": title,
		"severity": severity,
		"result": result,
		"record_count": len(rows),
		"amount": float(amount or 0),
		"message": message,
		"evidence_json": _evidence(rows, **evidence),
		"checked_on": now_datetime(),
	}


def _kamra_version() -> str:
	try:
		import kamra

		return str(getattr(kamra, "__version__", "unknown"))
	except Exception:
		return "unavailable"


def _compatibility_check() -> tuple[dict, str]:
	installed = set(frappe.get_installed_apps())
	missing_apps = sorted({"kamra", "erpnext"} - installed)
	missing_doctypes = []
	missing_fields = []
	for doctype, fields in REQUIRED_SCHEMA.items():
		if not frappe.db.exists("DocType", doctype):
			missing_doctypes.append(doctype)
			continue
		meta = frappe.get_meta(doctype)
		for fieldname in sorted(fields):
			if not meta.has_field(fieldname):
				missing_fields.append(f"{doctype}.{fieldname}")

	version = _kamra_version()
	version_mismatch = version != EXPECTED_KAMRA_VERSION
	failures = missing_apps + missing_doctypes + missing_fields
	if version_mismatch:
		failures.append(f"Kamra version {version}; expected {EXPECTED_KAMRA_VERSION}")
	result = "Failed" if failures else "Passed"
	message = (
		"Required Kamra/ERPNext compatibility contract is satisfied."
		if not failures
		else "Compatibility contract failed; do not execute night audit."
	)
	return (
		_check(
			"COMPATIBILITY",
			"Platform",
			"Installed application and schema contract",
			"Blocker",
			result,
			message,
			rows=[{"failure": value} for value in failures],
			installed_apps=sorted(installed),
			expected_kamra_version=EXPECTED_KAMRA_VERSION,
			actual_kamra_version=version,
		),
		version,
	)


def _get_settings() -> dict:
	if not frappe.db.exists("DocType", "Hotel Control Settings"):
		return {
			"earliest_night_audit_time": "02:00:00",
			"allowed_unreconciled_variance": 0,
			"currency": "NGN",
			"timezone": "Africa/Lagos",
		}
	doc = frappe.get_single("Hotel Control Settings")
	return {
		"earliest_night_audit_time": str(doc.earliest_night_audit_time or "02:00:00"),
		"allowed_unreconciled_variance": flt(doc.allowed_unreconciled_variance),
		"currency": doc.currency or "NGN",
		"timezone": doc.timezone or "Africa/Lagos",
	}


def _collect(property_name: str, business_date) -> dict:
	property_doc = frappe.get_doc("Property", property_name)
	property_data = {
		"name": property_doc.name,
		"country": property_doc.country,
		"timezone": property_doc.timezone,
		"currency": property_doc.currency,
		"no_show_charge": property_doc.no_show_charge,
		"disabled": property_doc.disabled,
		"require_cashier_pin": property_doc.require_cashier_pin,
		"modified": property_doc.modified,
	}

	reservations = _rows(
		"Reservation",
		filters={"property": property_name},
		fields=[
			"name", "modified", "guest", "guest_name", "status", "booking_type", "company",
			"source", "channel", "ota_ref", "room", "room_type", "meal_plan",
			"check_in_date", "check_out_date", "amount_after_tax", "advance_paid",
			"discount_amount", "is_pay_at_hotel",
		],
	)
	folios = _rows(
		"Folio",
		filters={"property": property_name},
		fields=[
			"name", "modified", "reservation", "guest", "status", "folio_type",
			"charges_total", "tax_total", "grand_total", "payments_total", "balance",
			"invoice_number", "opened_on", "closed_on",
		],
	)
	folio_names = [row["name"] for row in folios]
	charges = (
		_rows(
			"Folio Charge",
			filters={"parent": ["in", folio_names]},
			fields=[
				"name", "modified", "parent", "idx", "posting_date", "charge_type",
				"description", "amount", "gst_rate", "gst_amount", "total",
				"auto_posted", "reservation",
			],
			order_by="parent asc, idx asc",
		)
		if folio_names
		else []
	)
	payments = (
		_rows(
			"Folio Payment",
			filters={"parent": ["in", folio_names]},
			fields=[
				"name", "modified", "parent", "idx", "posting_date", "payment_kind",
				"mode", "amount", "currency", "exchange_rate", "reference",
			],
			order_by="parent asc, idx asc",
		)
		if folio_names
		else []
	)
	pos_orders = _rows(
		"POS Order",
		filters={"property": property_name},
		fields=[
			"name", "creation", "modified", "outlet", "status", "room", "reservation",
			"order_total", "posted_to_folio", "discount_amount", "discount_reason",
			"paid", "payment_mode", "order_type", "nc", "nc_authorized_by",
		],
	)
	rooms = _rows(
		"Room",
		filters={"property": property_name},
		fields=[
			"name", "modified", "room_number", "room_type", "housekeeping_status",
			"occupancy_status",
		],
	)
	kamra_audits = _rows(
		"Night Audit Run",
		filters={"property": property_name, "business_date": str(business_date)},
		fields=["name", "creation", "status", "business_date", "room_charges_posted", "amount_posted"],
	)
	return {
		"property": property_data,
		"reservations": reservations,
		"folios": folios,
		"charges": charges,
		"payments": payments,
		"pos_orders": pos_orders,
		"rooms": rooms,
		"kamra_audits": kamra_audits,
	}


def _localization_check(data, settings):
	property_data = data["property"]
	expected = {
		"country": "Nigeria",
		"currency": settings["currency"],
		"timezone": settings["timezone"],
	}
	actual = {key: property_data.get(key) for key in expected}
	mismatches = [
		{"field": key, "expected": expected[key], "actual": actual[key]}
		for key in expected
		if actual[key] != expected[key]
	]
	return _check(
		"LOCALIZATION",
		"Platform",
		"Nigeria localization settings",
		"Warning",
		"Warning" if mismatches else "Passed",
		"Property localization must be corrected before production use." if mismatches else "Property localization matches hotel control settings.",
		rows=mismatches,
	)


def _business_date_check(data, settings, business_date):
	issues = []
	if business_date > getdate(today()):
		issues.append({"issue": "Business date is in the future."})
	if data["property"].get("disabled"):
		issues.append({"issue": "Property is disabled."})
	if data["kamra_audits"]:
		issues.append({"issue": "Kamra Night Audit Run already exists.", "runs": data["kamra_audits"]})

	if business_date == getdate(today()):
		earliest_text = str(settings["earliest_night_audit_time"] or "02:00:00").split(".")[0]
		parts = [int(part) for part in earliest_text.split(":")[:3]]
		while len(parts) < 3:
			parts.append(0)
		earliest = time(*parts)
		if now_datetime().time() < earliest:
			issues.append({"issue": f"Current time is before configured earliest audit time {earliest_text}."})
	return _check(
		"BUSINESS_DATE",
		"Front Office",
		"Business date, timing and duplicate-run gate",
		"Blocker",
		"Failed" if issues else "Passed",
		"Resolve the business-date gate before audit." if issues else "Business-date gate passed.",
		rows=issues,
		configured_earliest_time=settings["earliest_night_audit_time"],
	)


def _reservation_checks(data, business_date):
	reservations = data["reservations"]
	due_arrivals = [
		row for row in reservations
		if row["status"] in {"Confirmed", "Held", "Pending Payment"}
		and row.get("check_in_date") and getdate(row["check_in_date"]) <= business_date
	]
	due_departures = [
		row for row in reservations
		if row["status"] == "Checked In"
		and row.get("check_out_date") and getdate(row["check_out_date"]) <= business_date
	]
	in_house = [
		row for row in reservations
		if row["status"] == "Checked In"
		and row.get("check_in_date") and getdate(row["check_in_date"]) <= business_date
		and row.get("check_out_date") and getdate(row["check_out_date"]) > business_date
	]
	missing_rooms = [row for row in in_house if not row.get("room")]
	corporate_missing_company = [
		row for row in reservations
		if row["status"] not in {"Cancelled", "No Show", "Checked Out"}
		and row.get("booking_type") == "Corporate" and not row.get("company")
	]
	complimentary = [
		row for row in reservations
		if row["status"] in {"Confirmed", "Checked In"}
		and flt(row.get("amount_after_tax")) == 0
	]

	checks = [
		_check(
			"DUE_ARRIVALS", "Front Office", "Resolve expected arrivals and no-shows", "Blocker",
			"Failed" if due_arrivals else "Passed",
			"Expected arrivals remain unresolved." if due_arrivals else "No unresolved due arrivals.",
			rows=due_arrivals,
		),
		_check(
			"DUE_DEPARTURES", "Front Office", "Resolve due departures", "Blocker",
			"Failed" if due_departures else "Passed",
			"Checked-in reservations are past or at departure date." if due_departures else "No unresolved due departures.",
			rows=due_departures,
		),
		_check(
			"IN_HOUSE_ROOM_ASSIGNMENT", "Front Office", "Every in-house guest has an assigned room", "Blocker",
			"Failed" if missing_rooms else "Passed",
			"In-house reservations are missing rooms." if missing_rooms else "All in-house reservations have rooms.",
			rows=missing_rooms,
		),
		_check(
			"CORPORATE_MASTER_DATA", "Credit", "Corporate bookings have a company account", "Blocker",
			"Failed" if corporate_missing_company else "Passed",
			"Corporate reservations are missing Company." if corporate_missing_company else "Corporate booking master data is complete.",
			rows=corporate_missing_company,
		),
		_check(
			"COMPLIMENTARY_ROOMS", "Approvals", "Complimentary and zero-value stays", "Warning",
			"Warning" if complimentary else "Passed",
			"Zero-value reservations require documented approval." if complimentary else "No zero-value active reservations detected.",
			rows=complimentary,
		),
	]
	return checks, in_house, due_departures


def _folio_checks(data, in_house, due_departures, tolerance):
	folios = data["folios"]
	charges = data["charges"]
	payments = data["payments"]
	folios_by_reservation = defaultdict(list)
	for folio in folios:
		folios_by_reservation[folio.get("reservation")].append(folio)

	missing = []
	duplicates = []
	closed_in_house = []
	for reservation in in_house:
		guest_folios = [
			folio for folio in folios_by_reservation.get(reservation["name"], [])
			if folio.get("folio_type") == "Guest"
		]
		if not guest_folios:
			missing.append(reservation)
		if len(guest_folios) > 1:
			duplicates.append({"reservation": reservation["name"], "folios": [row["name"] for row in guest_folios]})
		closed_in_house.extend(
			{"reservation": reservation["name"], "folio": row["name"]}
			for row in guest_folios if row.get("status") == "Closed"
		)
	coverage_issues = missing + duplicates + closed_in_house

	charges_by_parent = defaultdict(list)
	payments_by_parent = defaultdict(list)
	for row in charges:
		charges_by_parent[row["parent"]].append(row)
	for row in payments:
		payments_by_parent[row["parent"]].append(row)

	arithmetic_issues = []
	for folio in folios:
		computed_charges = sum(Decimal(str(row.get("amount") or 0)) for row in charges_by_parent[folio["name"]])
		computed_tax = sum(
			Decimal(str(row.get("amount") or 0)) * Decimal(str(row.get("gst_rate") or 0)) / Decimal(100)
			for row in charges_by_parent[folio["name"]]
		)
		computed_total = computed_charges + computed_tax
		computed_paid = sum(Decimal(str(row.get("amount") or 0)) for row in payments_by_parent[folio["name"]])
		computed_balance = computed_total - computed_paid
		differences = {
			"charges_total": float(computed_charges - Decimal(str(folio.get("charges_total") or 0))),
			"tax_total": float(computed_tax - Decimal(str(folio.get("tax_total") or 0))),
			"grand_total": float(computed_total - Decimal(str(folio.get("grand_total") or 0))),
			"payments_total": float(computed_paid - Decimal(str(folio.get("payments_total") or 0))),
			"balance": float(computed_balance - Decimal(str(folio.get("balance") or 0))),
		}
		if any(abs(value) > tolerance for value in differences.values()):
			arithmetic_issues.append({"folio": folio["name"], "differences": differences})

	due_names = {row["name"] for row in due_departures}
	unpaid_departures = [
		folio for folio in folios
		if folio.get("reservation") in due_names and flt(folio.get("balance")) > tolerance
	]
	negative_balances = [
		folio for folio in folios
		if folio.get("status") == "Open" and flt(folio.get("balance")) < -tolerance
	]

	reference_modes = {"Card", "UPI", "Bank Transfer", "OTA Prepaid", "Payment Link"}
	missing_references = [
		row for row in payments
		if row.get("mode") in reference_modes and not str(row.get("reference") or "").strip()
	]
	reservation_map = {row["name"]: row for row in data["reservations"]}
	folio_map = {row["name"]: row for row in folios}
	invalid_company_credit = []
	for payment in payments:
		if payment.get("mode") != "Company Credit":
			continue
		folio = folio_map.get(payment["parent"]) or {}
		reservation = reservation_map.get(folio.get("reservation")) or {}
		if reservation.get("booking_type") != "Corporate" or not reservation.get("company"):
			invalid_company_credit.append({
				"payment": payment["name"], "folio": payment["parent"],
				"reservation": reservation.get("name"), "company": reservation.get("company"),
			})

	refunds = [row for row in payments if row.get("payment_kind") == "Refund"]
	refunds_missing_reference = [row for row in refunds if not str(row.get("reference") or "").strip()]

	return [
		_check(
			"FOLIO_COVERAGE", "Folio", "In-house guest folio coverage", "Blocker",
			"Failed" if coverage_issues else "Passed",
			"Missing, duplicate, or closed in-house guest folios detected." if coverage_issues else "In-house folio coverage is valid.",
			rows=coverage_issues,
		),
		_check(
			"FOLIO_ARITHMETIC", "Folio", "Folio stored totals match source rows", "Blocker",
			"Failed" if arithmetic_issues else "Passed",
			"Stored folio totals differ from charge/payment rows." if arithmetic_issues else "Folio arithmetic is internally consistent.",
			rows=arithmetic_issues,
		),
		_check(
			"UNPAID_DEPARTURES", "Credit", "Due departures have acceptable balances", "Blocker",
			"Failed" if unpaid_departures else "Passed",
			"Due departures have unpaid folio balances." if unpaid_departures else "No unpaid due departures.",
			rows=unpaid_departures,
			amount=sum(flt(row.get("balance")) for row in unpaid_departures),
			tolerance=tolerance,
		),
		_check(
			"NEGATIVE_FOLIOS", "Folio", "Open folios with negative balances", "Warning",
			"Warning" if negative_balances else "Passed",
			"Open folios contain credits or overpayments requiring review." if negative_balances else "No abnormal negative open-folio balances.",
			rows=negative_balances,
			amount=sum(flt(row.get("balance")) for row in negative_balances),
		),
		_check(
			"PAYMENT_REFERENCES", "Payments", "Non-cash tender references", "Blocker",
			"Failed" if missing_references else "Passed",
			"Non-cash payments are missing references." if missing_references else "Non-cash tender references are present.",
			rows=missing_references,
		),
		_check(
			"COMPANY_CREDIT", "Credit", "Company-credit tender integrity", "Blocker",
			"Failed" if invalid_company_credit else "Passed",
			"Company Credit is not linked to a valid corporate reservation/company." if invalid_company_credit else "Company-credit tenders have corporate accounts.",
			rows=invalid_company_credit,
		),
		_check(
			"REFUND_CONTROL", "Approvals", "Refund references and approval evidence", "Warning",
			"Failed" if refunds_missing_reference else ("Warning" if refunds else "Passed"),
			"Refunds missing references require correction." if refunds_missing_reference else ("Refunds require approval evidence review." if refunds else "No refunds detected."),
			rows=refunds_missing_reference or refunds,
		),
	]


def _pos_checks(data):
	orders = [row for row in data["pos_orders"] if row.get("status") != "Cancelled"]
	open_orders = [row for row in orders if row.get("status") in {"Placed", "Confirmed", "Preparing"}]
	unsettled = [
		row for row in orders
		if row.get("status") == "Delivered" and not row.get("paid") and not row.get("posted_to_folio")
	]
	folio_posting_issues = [
		row for row in orders
		if row.get("posted_to_folio") and not row.get("reservation")
	]
	control_issues = []
	for row in orders:
		if flt(row.get("discount_amount")) > 0 and not str(row.get("discount_reason") or "").strip():
			control_issues.append({"order": row["name"], "issue": "Discount has no reason."})
		if row.get("nc") and not str(row.get("nc_authorized_by") or "").strip():
			control_issues.append({"order": row["name"], "issue": "Complimentary order has no authorizer."})
	return [
		_check(
			"OPEN_POS_ORDERS", "Outlets", "Restaurant and outlet orders are closed", "Blocker",
			"Failed" if open_orders else "Passed",
			"Open POS orders must be resolved." if open_orders else "No open POS orders.",
			rows=open_orders,
		),
		_check(
			"POS_SETTLEMENT", "Outlets", "Delivered orders are paid or posted to folio", "Blocker",
			"Failed" if unsettled else "Passed",
			"Delivered POS orders are neither paid nor posted to a folio." if unsettled else "Delivered orders are settled or transferred.",
			rows=unsettled,
			amount=sum(flt(row.get("order_total")) for row in unsettled),
		),
		_check(
			"POS_FOLIO_LINKS", "Outlets", "Room-charge POS orders have reservations", "Blocker",
			"Failed" if folio_posting_issues else "Passed",
			"POS orders marked as posted to folio lack a reservation." if folio_posting_issues else "POS folio links are structurally valid.",
			rows=folio_posting_issues,
		),
		_check(
			"POS_APPROVAL_FIELDS", "Approvals", "POS discount and complimentary controls", "Blocker",
			"Failed" if control_issues else "Passed",
			"POS discounts or complimentary orders lack required reason/authorizer." if control_issues else "POS discount and complimentary fields are complete.",
			rows=control_issues,
		),
	]


def _room_status_check(data, in_house):
	room_map = {row["name"]: row for row in data["rooms"]}
	assigned = {row.get("room"): row for row in in_house if row.get("room")}
	issues = []
	for room_name, reservation in assigned.items():
		room = room_map.get(room_name)
		if not room:
			issues.append({"reservation": reservation["name"], "room": room_name, "issue": "Assigned room does not exist at property."})
			continue
		if room.get("occupancy_status") != "Occupied":
			issues.append({"reservation": reservation["name"], "room": room_name, "issue": "Reservation is checked in but room is not Occupied.", "room_status": room.get("occupancy_status")})
		if room.get("housekeeping_status") == "Out of Order":
			issues.append({"reservation": reservation["name"], "room": room_name, "issue": "Occupied room is Out of Order."})
	for room in data["rooms"]:
		if room.get("occupancy_status") == "Occupied" and room["name"] not in assigned:
			issues.append({"room": room["name"], "issue": "Room is Occupied without a matching in-house reservation."})
	return _check(
		"ROOM_STATUS_RECONCILIATION",
		"Housekeeping",
		"Reservation and room occupancy status agree",
		"Blocker",
		"Failed" if issues else "Passed",
		"Front-office and room-status differences detected." if issues else "Reservation and room occupancy status agree.",
		rows=issues,
	)


def _room_charge_preview(data, in_house, business_date):
	existing = {
		(row.get("reservation"), str(row.get("posting_date")))
		for row in data["charges"] if row.get("charge_type") == "Room"
	}
	preview = []
	errors = []
	try:
		from kamra.folio import _nightly_gst, _nightly_room_rate
		for row in in_house:
			if (row["name"], str(business_date)) in existing:
				continue
			try:
				rate = flt(_nightly_room_rate(frappe.get_doc("Reservation", row["name"]), business_date))
				gst_rate = flt(_nightly_gst(frappe.get_doc("Reservation", row["name"]), business_date))
				preview.append({
					"reservation": row["name"], "room": row.get("room"),
					"rate": rate, "gst_rate": gst_rate, "tax": rate * gst_rate / 100,
				})
			except Exception as exc:
				errors.append({"reservation": row["name"], "error": str(exc)})
	except Exception as exc:
		errors.append({"error": str(exc)})
	if errors:
		return _check(
			"ROOM_CHARGE_PREVIEW", "Revenue", "Preview unposted room-night charges", "Blocker", "Error",
			"One or more room-night charges could not be priced.", rows=errors,
		)
	amount = sum(row["rate"] + row["tax"] for row in preview)
	return _check(
		"ROOM_CHARGE_PREVIEW", "Revenue", "Preview unposted room-night charges", "Information", "Passed",
		f"{len(preview)} room-night charge(s) would be posted by a later controlled execution phase.",
		rows=preview, amount=amount,
	)


def _tender_summary(data, business_date):
	tenders = defaultdict(Decimal)
	for row in data["payments"]:
		if row.get("posting_date") and getdate(row["posting_date"]) == business_date:
			tenders[row.get("mode") or "Unspecified"] += Decimal(str(row.get("amount") or 0))
	rows = [{"mode": key, "amount": float(value)} for key, value in sorted(tenders.items())]
	return _check(
		"PMS_TENDER_SUMMARY", "Payments", "PMS tender totals for business date", "Information", "Passed",
		"PMS tender totals captured for independent reconciliation.", rows=rows,
		amount=sum(tenders.values()),
	)


def _stale_waitlist_check(data, business_date):
	stale = [
		row for row in data["reservations"]
		if row.get("status") == "Waitlist" and row.get("check_out_date")
		and getdate(row["check_out_date"]) <= frappe.utils.add_days(business_date, -2)
	]
	return _check(
		"STALE_WAITLIST", "Data Retention", "Stale waitlist deletion risk", "Warning",
		"Warning" if stale else "Passed",
		"Kamra's current built-in audit would permanently delete these records; controlled execution must archive instead." if stale else "No stale waitlist records are exposed to built-in deletion.",
		rows=stale,
	)


def _scope_gap_checks():
	return [
		_check(
			"CASHIER_CLOSE", "Payments", "Cashier shift and physical cash close", "Warning", "Skipped",
			"Kamra 2.5.0 has no cashier-close record in the mapped schema. Manual evidence is required in v0.3.0.",
		),
		_check(
			"ERP_RECONCILIATION", "Finance", "PMS revenue and tenders reconcile to ERPNext", "Warning", "Skipped",
			"ERPNext posting and reconciliation are not enabled in v0.3.0.",
		),
		_check(
			"BACKUP_EVIDENCE", "IT", "Successful recoverable backup before date close", "Warning", "Skipped",
			"Backup evidence is not yet linked to the preflight engine; attach and review it manually.",
		),
		_check(
			"APPROVAL_LEDGER", "Approvals", "Refund, void, discount and complimentary approval ledger", "Warning", "Skipped",
			"Generic approval-ledger integration is not yet complete; detected exceptions require manual evidence review.",
		),
	]


def _evaluate(data, settings, business_date, compatibility):
	checks = [compatibility, _localization_check(data, settings), _business_date_check(data, settings, business_date)]
	reservation_checks, in_house, due_departures = _reservation_checks(data, business_date)
	checks.extend(reservation_checks)
	checks.extend(_folio_checks(data, in_house, due_departures, flt(settings["allowed_unreconciled_variance"])))
	checks.extend(_pos_checks(data))
	checks.append(_room_status_check(data, in_house))
	checks.append(_room_charge_preview(data, in_house, business_date))
	checks.append(_tender_summary(data, business_date))
	checks.append(_stale_waitlist_check(data, business_date))
	checks.extend(_scope_gap_checks())
	return checks


def _outcome(checks):
	hard_blockers = sum(
		row["severity"] == "Blocker" and row["result"] in {"Failed", "Error"}
		for row in checks
	)
	if hard_blockers:
		return "Blocked", hard_blockers
	if any(row["result"] in {"Warning", "Skipped", "Failed", "Error"} for row in checks):
		return "Review Required", 0
	return "Passed", 0


def _summary(checks, data, settings, reused=False):
	return {
		"engine_version": ENGINE_VERSION,
		"read_only": True,
		"reused_unchanged_snapshot": reused,
		"source_counts": {
			"reservations": len(data.get("reservations", [])),
			"folios": len(data.get("folios", [])),
			"folio_charges": len(data.get("charges", [])),
			"folio_payments": len(data.get("payments", [])),
			"pos_orders": len(data.get("pos_orders", [])),
			"rooms": len(data.get("rooms", [])),
		},
		"settings": settings,
		"result_counts": {
			result: sum(row["result"] == result for row in checks)
			for result in ("Passed", "Failed", "Warning", "Skipped", "Error")
		},
	}


def _insert_control(property_name, business_date, checks, data, settings, source_hash, kamra_version, started_on):
	status, hard_blockers = _outcome(checks)
	attempt = frappe.db.count(
		"Hotel Night Audit Control",
		filters={"property": property_name, "business_date": str(business_date)},
	) + 1
	summary = _summary(checks, data, settings)
	doc = frappe.new_doc("Hotel Night Audit Control")
	doc.property = property_name
	doc.business_date = business_date
	doc.attempt_number = attempt
	doc.status = status
	doc.scope = "PMS Read-only Preflight"
	doc.execution_permitted = 0
	doc.started_on = started_on
	doc.completed_on = now_datetime()
	doc.executed_by = frappe.session.user
	doc.engine_version = ENGINE_VERSION
	doc.kamra_version = kamra_version
	doc.source_hash = source_hash
	doc.total_checks = len(checks)
	doc.passed_checks = sum(row["result"] == "Passed" for row in checks)
	doc.failed_checks = sum(row["result"] == "Failed" for row in checks)
	doc.warning_checks = sum(row["result"] == "Warning" for row in checks)
	doc.skipped_checks = sum(row["result"] == "Skipped" for row in checks)
	doc.error_checks = sum(row["result"] == "Error" for row in checks)
	doc.hard_blockers = hard_blockers
	doc.summary_json = json.dumps(summary, sort_keys=True, default=str, ensure_ascii=False, indent=2)
	doc.engine_message = (
		"Hard blockers must be resolved; no execution was attempted."
		if status == "Blocked"
		else "Manual or out-of-scope evidence must be reviewed; no execution was attempted."
		if status == "Review Required"
		else "Current read-only PMS preflight passed; this still does not authorize execution."
	)
	for sequence, row in enumerate(checks, start=1):
		row = dict(row)
		row["sequence"] = sequence
		doc.append("checks", row)
	doc.flags.from_night_audit_engine = True
	doc.insert(ignore_permissions=True)
	return doc


def _result(doc, reused=False):
	return {
		"control": doc.name,
		"property": doc.property,
		"business_date": str(doc.business_date),
		"attempt_number": doc.attempt_number,
		"status": doc.status,
		"hard_blockers": doc.hard_blockers,
		"total_checks": doc.total_checks,
		"passed_checks": doc.passed_checks,
		"failed_checks": doc.failed_checks,
		"warning_checks": doc.warning_checks,
		"skipped_checks": doc.skipped_checks,
		"error_checks": doc.error_checks,
		"execution_permitted": False,
		"reused_unchanged_snapshot": reused,
	}


def run_read_only_preflight(property_name: str, business_date=None) -> dict:
	"""Run evidence-backed checks without changing Kamra or ERPNext records."""
	if not property_name or not frappe.db.exists("Property", property_name):
		frappe.throw(_("Unknown hotel property: {0}").format(property_name or "(blank)"))
	business_date = getdate(business_date or today())
	lock_name = "hnac:" + hashlib.sha256(f"{frappe.local.site}|{property_name}|{business_date}".encode()).hexdigest()[:50]
	acquired = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock_name, 10))[0][0]
	if acquired != 1:
		frappe.throw(_("Another preflight is already running for this property and business date. Please retry."))
	started_on = now_datetime()
	try:
		compatibility, kamra_version = _compatibility_check()
		if compatibility["result"] == "Failed":
			data = {"property": {"name": property_name}, "reservations": [], "folios": [], "charges": [], "payments": [], "pos_orders": [], "rooms": [], "kamra_audits": []}
			settings = _get_settings()
			checks = [compatibility]
		else:
			settings = _get_settings()
			data = _collect(property_name, business_date)
			checks = _evaluate(data, settings, business_date, compatibility)

		fingerprint_payload = {
			"engine_version": ENGINE_VERSION,
			"property": property_name,
			"business_date": str(business_date),
			"settings": settings,
			"data": data,
			"checks": [{key: value for key, value in row.items() if key != "checked_on"} for row in checks],
		}
		source_hash = hashlib.sha256(_json(fingerprint_payload).encode()).hexdigest()
		existing = frappe.db.get_value("Hotel Night Audit Control", {"source_hash": source_hash}, "name")
		if existing:
			return _result(frappe.get_doc("Hotel Night Audit Control", existing), reused=True)

		doc = _insert_control(
			property_name, business_date, checks, data, settings, source_hash, kamra_version, started_on
		)
		return _result(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Hotel Night Audit Preflight failed")
		raise
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_name,))

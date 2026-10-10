"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import re
import calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is missing from .env")

client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = {"PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"}

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
)


@app.on_event("startup")
def ensure_indexes():
    db.employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="unique_emp_code",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="unique_employee_attendance_date",
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="attendance_date_employee",
    )
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)],
        name="attendance_emp_punchin",
    )
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", ASCENDING)],
        name="attendance_status_date",
    )
    db.employees.create_index(
        [("department", ASCENDING), ("joined_on", ASCENDING)],
        name="department_joined_on",
    )


def utc_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(microsecond=0)


def parse_epoch(ms: Optional[int]) -> datetime:
    if ms is None:
        return datetime.now(UTC).replace(microsecond=0)
    return datetime.fromtimestamp(ms / 1000, tz=UTC).replace(microsecond=0)


def epoch_ms(dt: Optional[datetime]):
    if dt is None:
        return None
    return int(utc_dt(dt).timestamp() * 1000)


def local(dt: datetime) -> datetime:
    return utc_dt(dt).astimezone(IST)


def half_up(value: float, places: int = 2) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    )


def shift_start_at(day: str, shift_start: str) -> datetime:
    d = date.fromisoformat(day)
    h, m = map(int, shift_start.split(":"))
    return datetime.combine(d, time(h, m), IST).astimezone(UTC)


def shift_end_at(day: str, shift_start: str, shift_end: str) -> datetime:
    d = date.fromisoformat(day)
    if shift_end <= shift_start:
        d += timedelta(days=1)
    h, m = map(int, shift_end.split(":"))
    return datetime.combine(d, time(h, m), IST).astimezone(UTC)


def attendance_date(
    punch_in: datetime,
    shift_start: str,
    shift_end: str,
) -> str:
    lt = local(punch_in)
    if shift_end <= shift_start and lt.strftime("%H:%M") < shift_end:
        lt -= timedelta(days=1)
    return lt.date().isoformat()


def late_minutes(
    punch_in: Optional[datetime],
    day: str,
    shift_start: str,
) -> int:
    if punch_in is None:
        return 0

    seconds = (
        utc_dt(punch_in) - shift_start_at(day, shift_start)
    ).total_seconds()

    return int(seconds // 60) if seconds > 600 else 0


def work_hours(
    punch_in: Optional[datetime],
    punch_out: Optional[datetime],
):
    if punch_in is None or punch_out is None:
        return None

    seconds = (
        utc_dt(punch_out) - utc_dt(punch_in)
    ).total_seconds()

    return half_up(seconds / 3600, 2)


def overtime_minutes(
    punch_out: Optional[datetime],
    day: str,
    shift_start: str,
    shift_end: str,
) -> int:
    if punch_out is None:
        return 0

    seconds = (
        utc_dt(punch_out)
        - shift_end_at(day, shift_start, shift_end)
    ).total_seconds()

    minutes = int(seconds // 60)
    return minutes if minutes >= 30 else 0


def derived(record: dict, employee: dict) -> dict:
    if record.get("status") not in PRESENCE:
        return {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }

    punch_in = record.get("punch_in")
    punch_out = record.get("punch_out")

    hours = work_hours(punch_in, punch_out)

    return {
        "late_minutes": late_minutes(
            punch_in,
            record["date"],
            employee["shift_start"],
        ),
        "work_hours": hours,
        "overtime_minutes": overtime_minutes(
            punch_out,
            record["date"],
            employee["shift_start"],
            employee["shift_end"],
        ),
        "half_day": hours is not None and hours < 4.50,
    }


def api_record(doc: dict) -> dict:
    history = []

    for entry in doc.get("history", []):
        item = dict(entry)
        item["at"] = epoch_ms(item.get("at"))

        converted_changes = {}

        for key, value in (item.get("changes") or {}).items():
            before = value.get("from")
            after = value.get("to")

            if key in ("punch_in", "punch_out"):
                before = epoch_ms(before) if before is not None else None
                after = epoch_ms(after) if after is not None else None

            converted_changes[key] = {
                "from": before,
                "to": after,
            }

        item["changes"] = converted_changes
        history.append(item)

    return {
        "emp_code": doc.get("emp_code"),
        "date": doc.get("date"),
        "status": doc.get("status"),
        "punch_in": epoch_ms(doc.get("punch_in")),
        "punch_out": epoch_ms(doc.get("punch_out")),
        "work_hours": doc.get("work_hours"),
        "late_minutes": int(doc.get("late_minutes", 0) or 0),
        "overtime_minutes": int(doc.get("overtime_minutes", 0) or 0),
        "half_day": bool(doc.get("half_day", False)),
        "history": history,
    }


def api_employee(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}

    if "created_at" in result:
        result["created_at"] = epoch_ms(result["created_at"])

    return result


def month_range(month: str):
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM",
        )

    try:
        year, month_number = map(int, month.split("-"))
        start = date(year, month_number, 1)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="month must use YYYY-MM",
        )

    end = date(
        year,
        month_number,
        calendar.monthrange(year, month_number)[1],
    )

    return start.isoformat(), end.isoformat(), start, end


def weekdays(start: date, end: date) -> int:
    if end < start:
        return 0

    count = 0
    current = start

    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)

    return count


class EmployeeIn(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="ignore",
    )

    emp_code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    department: str = Field(min_length=1)
    shift_start: str = "09:00"
    shift_end: str = "18:00"
    joined_on: str

    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_time(cls, value):
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("time must use HH:MM")
        return value

    @field_validator("joined_on")
    @classmethod
    def valid_date(cls, value):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD")

        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD")

        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(min_length=1)
    punched_at: Optional[datetime] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(min_length=1)
    punched_at: Optional[datetime] = None


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None
    punch_in: Optional[datetime] = None
    punch_out: Optional[datetime] = None
    by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(UTC).replace(microsecond=0)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists",
        )

    return api_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department else {}

    total = db.employees.count_documents(query)

    documents = (
        db.employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [api_employee(doc) for doc in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})

    if not employee:
        raise HTTPException(status_code=404, detail="unknown employee")

    punched_at = utc_dt(body.punched_at or datetime.now(UTC))
    day = attendance_date(
        punched_at,
        employee["shift_start"],
        employee["shift_end"],
    )

    document = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": punched_at,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes(
            punched_at,
            day,
            employee["shift_start"],
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(document)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for that date",
        )

    return api_record(document)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})

    if not employee:
        raise HTTPException(status_code=404, detail="unknown employee")

    punched_at = utc_dt(body.punched_at or datetime.now(UTC))

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$ne": None, "$lte": punched_at},
            "punch_out": None,
            "status": {"$in": list(PRESENCE)},
        },
        sort=[("punch_in", DESCENDING)],
    )

    if not record:
        previous = db.attendance_logs.find_one(
            {
                "emp_code": body.emp_code,
                "punch_in": {"$ne": None, "$lte": punched_at},
                "punch_out": {"$ne": None},
            },
            sort=[("punch_in", DESCENDING)],
        )

        if previous:
            raise HTTPException(
                status_code=409,
                detail="record already punched out",
            )

        raise HTTPException(
            status_code=404,
            detail="no punch-in found",
        )

    punch_in_time = utc_dt(record["punch_in"])

    if punched_at <= punch_in_time:
        raise HTTPException(
            status_code=422,
            detail="punched_at must be after punch_in",
        )

    if punched_at - punch_in_time > timedelta(hours=24):
        raise HTTPException(
            status_code=422,
            detail="punch-out must be within 24 hours",
        )

    calculated = derived(
        {**record, "punch_out": punched_at},
        employee,
    )

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": punched_at,
                **calculated,
            }
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(
            status_code=409,
            detail="record already punched out",
        )

    return api_record(updated)


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if date_from:
        try:
            date.fromisoformat(date_from)
        except ValueError:
            raise HTTPException(422, detail="date_from must use YYYY-MM-DD")

    if date_to:
        try:
            date.fromisoformat(date_to)
        except ValueError:
            raise HTTPException(422, detail="date_to must use YYYY-MM-DD")

    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be <= date_to",
        )

    query = {}

    if emp_code:
        query["emp_code"] = emp_code

    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to

    if status:
        query["status"] = status

    total = db.attendance_logs.count_documents(query)

    documents = (
        db.attendance_logs.find(query)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    return {
        "items": [api_record(doc) for doc in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/attendance/{emp_code}/{day}")
def get_attendance(emp_code: str, day: str):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD",
        )

    record = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": day}
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="attendance record not found",
        )

    return api_record(record)


@app.patch("/attendance/{emp_code}/{day}")
def regularize_attendance(
    emp_code: str,
    day: str,
    body: RegularizeIn,
):
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date must use YYYY-MM-DD",
        )

    employee = db.employees.find_one({"emp_code": emp_code})

    if not employee:
        raise HTTPException(status_code=404, detail="unknown employee")

    old = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": day}
    )

    if not old:
        raise HTTPException(
            status_code=404,
            detail="attendance record not found",
        )

    fields_set = body.model_fields_set
    final = dict(old)

    if "status" in fields_set:
        final["status"] = body.status

    if "punch_in" in fields_set:
        final["punch_in"] = (
            utc_dt(body.punch_in) if body.punch_in is not None else None
        )

    if "punch_out" in fields_set:
        final["punch_out"] = (
            utc_dt(body.punch_out) if body.punch_out is not None else None
        )

    if final["status"] in {"ABSENT", "LEAVE"}:
        if (
            ("punch_in" in fields_set and body.punch_in is not None)
            or ("punch_out" in fields_set and body.punch_out is not None)
        ):
            raise HTTPException(
                status_code=422,
                detail="ABSENT/LEAVE cannot include punch times",
            )

        final["punch_in"] = None
        final["punch_out"] = None
    else:
        if final.get("punch_in") is None:
            raise HTTPException(
                status_code=422,
                detail="presence status requires punch_in",
            )

        if attendance_date(
            final["punch_in"],
            employee["shift_start"],
            employee["shift_end"],
        ) != day:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to attendance date",
            )

        if final.get("punch_out") is not None:
            if utc_dt(final["punch_out"]) <= utc_dt(final["punch_in"]):
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be after punch_in",
                )

            if (
                utc_dt(final["punch_out"]) - utc_dt(final["punch_in"])
                > timedelta(hours=24)
            ):
                raise HTTPException(
                    status_code=422,
                    detail="punch_out must be within 24 hours",
                )

    final.update(derived(final, employee))

    keys = [
        "status",
        "punch_in",
        "punch_out",
        "work_hours",
        "late_minutes",
        "overtime_minutes",
        "half_day",
    ]

    changes = {}

    for key in keys:
        before = old.get(key)
        after = final.get(key)

        if key in ("late_minutes", "overtime_minutes"):
            before, after = int(before or 0), int(after or 0)

        if key == "half_day":
            before, after = bool(before), bool(after)

        if before != after:
            changes[key] = {"from": before, "to": after}

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="request does not change the record",
        )

    history = old.get("history", [])

    history_entry = {
        "at": datetime.now(UTC).replace(microsecond=0),
        "by": body.by,
        "reason": body.reason,
        "changes": changes,
    }

    updated = db.attendance_logs.find_one_and_update(
        {
            "_id": old["_id"],
            "history": history,
        },
        {
            "$set": {key: final.get(key) for key in keys},
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )

    if not updated:
        raise HTTPException(
            status_code=409,
            detail="record changed concurrently; retry",
        )

    return api_record(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    employee = db.employees.find_one({"emp_code": emp_code})

    if not employee:
        raise HTTPException(status_code=404, detail="unknown employee")

    start_s, end_s, start, end = month_range(month)

    joined_on = date.fromisoformat(employee["joined_on"])
    first_day = max(start, joined_on)
    working_days = weekdays(first_day, end)

    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": start_s, "$lte": end_s},
            }
        },
        {
            "$group": {
                "_id": None,
                "records": {
                    "$push": {
                        "date": "$date",
                        "status": "$status",
                        "half_day": {"$ifNull": ["$half_day", False]},
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                },
            }
        },
    ]

    grouped = list(db.attendance_logs.aggregate(pipeline))
    summary = grouped[0] if grouped else {}

    present_days = 0.0

    for record in summary.get("records", []):
        record_day = date.fromisoformat(record["date"])

        if record["status"] in PRESENCE and record_day.weekday() < 5:
            present_days += 0.5 if record.get("half_day") else 1.0

    return {
        "emp_code": emp_code,
        "name": employee["name"],
        "department": employee["department"],
        "month": month,
        "working_days": working_days,
        "present_days": half_up(present_days),
        "leave_days": summary.get("leave_days", 0),
        "late_count": summary.get("late_count", 0),
        "total_late_minutes": summary.get("total_late_minutes", 0),
        "overtime_minutes": summary.get("total_overtime_minutes", 0),
        "attendance_pct": (
            half_up(present_days / working_days * 100)
            if working_days
            else None
        ),
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    start_s, end_s, _, _ = month_range(month)

    employee_docs = list(
        db.employees.find(
            {"joined_on": {"$lte": end_s}},
            {"_id": 0, "emp_code": 1, "department": 1},
        )
    )

    if not employee_docs:
        return []

    codes = [employee["emp_code"] for employee in employee_docs]

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": codes},
                "date": {"$gte": start_s, "$lte": end_s},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "records": {
                    "$push": {
                        "date": "$date",
                        "status": "$status",
                        "half_day": {"$ifNull": ["$half_day", False]},
                        "work_hours": "$work_hours",
                        "late_minutes": {"$ifNull": ["$late_minutes", 0]},
                    }
                },
            }
        },
    ]

    grouped = list(db.attendance_logs.aggregate(pipeline))
    by_code = {item["_id"]: item["records"] for item in grouped}

    summaries = {}

    for employee in employee_docs:
        department = employee["department"]

        summary = summaries.setdefault(
            department,
            {
                "department": department,
                "headcount": 0,
                "present_days": 0.0,
                "hours": [],
                "late_count": 0,
                "total_late_minutes": 0,
                "leave_count": 0,
                "on_duty_count": 0,
            },
        )

        summary["headcount"] += 1

        for record in by_code.get(employee["emp_code"], []):
            is_weekday = date.fromisoformat(record["date"]).weekday() < 5

            if record["status"] in PRESENCE and is_weekday:
                summary["present_days"] += (
                    0.5 if record.get("half_day") else 1.0
                )

            if (
                record["status"] in PRESENCE
                and record.get("work_hours") is not None
            ):
                summary["hours"].append(record["work_hours"])

            late = record.get("late_minutes", 0) or 0

            if late > 0:
                summary["late_count"] += 1
                summary["total_late_minutes"] += late

            if record["status"] == "LEAVE":
                summary["leave_count"] += 1

            if record["status"] == "ON_DUTY":
                summary["on_duty_count"] += 1

    result = []

    for department in sorted(summaries):
        summary = summaries[department]
        hours = summary["hours"]

        result.append(
            {
                "department": department,
                "headcount": summary["headcount"],
                "present_days": half_up(summary["present_days"]),
                "avg_work_hours": (
                    half_up(sum(hours) / len(hours))
                    if hours
                    else None
                ),
                "late_count": summary["late_count"],
                "total_late_minutes": summary["total_late_minutes"],
                "leave_count": summary["leave_count"],
                "on_duty_count": summary["on_duty_count"],
            }
        )

    return result


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(10, ge=1, le=100),
    department: Optional[str] = None,
):
    start_s, end_s, _, _ = month_range(month)

    pipeline = [
        {
            "$match": {
                "date": {"$gte": start_s, "$lte": end_s},
                "late_minutes": {"$gt": 0},
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
    ]

    if department is not None:
        pipeline.append(
            {"$match": {"employee.department": department}}
        )

    pipeline.extend(
        [
            {"$sort": {"total_late_minutes": -1, "_id": 1}},
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
            {
                "$project": {
                    "_id": 0,
                    "rank": 1,
                    "emp_code": "$_id",
                    "name": "$employee.name",
                    "department": "$employee.department",
                    "total_late_minutes": 1,
                    "late_count": 1,
                }
            },
        ]
    )

    results = list(db.attendance_logs.aggregate(pipeline))

    return results


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    try:
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="from and to must use YYYY-MM-DD",
        )

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="to must be greater than or equal to from",
        )

    if (end - start).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="date range cannot exceed 92 days",
        )

    employee_docs = list(
        db.employees.find(
            {
                "department": department,
                "joined_on": {"$lte": to_date},
            },
            {"_id": 0, "emp_code": 1, "joined_on": 1},
        )
    )

    if not employee_docs:
        raise HTTPException(
            status_code=404,
            detail="unknown department",
        )

    employee_codes = [item["emp_code"] for item in employee_docs]

    start_dt = datetime.combine(start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(end, time.min, tzinfo=UTC)

    pipeline = [
        {
            "$match": {
                "emp_code": {"$in": employee_codes},
                "date": {"$gte": from_date, "$lte": to_date},
            }
        },
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", list(PRESENCE)]},
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1.0,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$gt": [
                                    {"$ifNull": ["$late_minutes", 0]},
                                    0,
                                ]
                            },
                            1,
                            0,
                        ]
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateFromString": {
                        "dateString": "$_id",
                        "format": "%Y-%m-%d",
                    }
                },
                "present_count": 1,
                "late_count": 1,
            }
        },
        {
            "$densify": {
                "field": "date",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt + timedelta(days=1)],
                },
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]},
                "date_string": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$date",
                        "timezone": "UTC",
                    }
                },
                "weekday": {"$dayOfWeek": "$date"},
            }
        },
        {
            "$set": {
                "is_working_day": {
                    "$and": [
                        {"$gte": ["$weekday", 2]},
                        {"$lte": ["$weekday", 6]},
                    ]
                },
                "headcount": {
                    "$size": {
                        "$filter": {
                            "input": employee_docs,
                            "as": "employee",
                            "cond": {
                                "$lte": [
                                    "$$employee.joined_on",
                                    "$date_string",
                                ]
                            },
                        }
                    }
                },
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]},
                            ]
                        },
                        {
                            "$divide": [
                                "$present_count",
                                "$headcount",
                            ]
                        },
                        None,
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": "$date_string",
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
        {"$sort": {"date": 1}},
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    for item in items:
        for field in ("attendance_rate", "moving_avg_7d"):
            value = item.get(field)
            if value is not None:
                item[field] = half_up(value, 4)

    return {
        "department": department,
        "items": items,
    }


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: str):
    import json
    from bson import json_util

    month = datetime.now(IST).strftime("%Y-%m")
    start_s, end_s, _, _ = month_range(month)

    specs = {
        "employees": (
            "employees",
            {
                "find": "employees",
                "filter": {},
                "sort": {"emp_code": 1},
                "limit": 20,
            },
        ),
        "attendance": (
            "attendance_logs",
            {
                "find": "attendance_logs",
                "filter": {},
                "sort": {"date": -1, "emp_code": 1},
                "limit": 20,
            },
        ),
        "employee_monthly": (
            "attendance_logs",
            {
                "aggregate": "attendance_logs",
                "pipeline": [
                    {
                        "$match": {
                            "date": {"$gte": start_s, "$lte": end_s}
                        }
                    },
                    {
                        "$group": {
                            "_id": "$emp_code",
                            "count": {"$sum": 1},
                        }
                    },
                ],
                "cursor": {},
            },
        ),
        "department_summary": (
            "employees",
            {
                "find": "employees",
                "filter": {"joined_on": {"$lte": end_s}},
            },
        ),
        "late_leaderboard": (
            "attendance_logs",
            {
                "find": "attendance_logs",
                "filter": {
                    "date": {"$gte": start_s, "$lte": end_s},
                    "late_minutes": {"$gt": 0},
                },
            },
        ),
        "department_trend": (
            "attendance_logs",
            {
                "find": "attendance_logs",
                "filter": {
                    "date": {"$gte": start_s, "$lte": end_s}
                },
            },
        ),
    }

    if endpoint not in specs:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported endpoint. Choose one of: {', '.join(specs)}",
        )

    collection, command = specs[endpoint]

    try:
        raw = db.command({
            "explain": command,
            "verbosity": "executionStats",
        })

        # Convert MongoDB BSON values to JSON-compatible values.
        result = json.loads(json_util.dumps(raw))

        return {
            "endpoint": endpoint,
            "collection": collection,
            "explain": result,
        }

    except Exception as exc:
        print(f"Explain endpoint error: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"MongoDB explain failed: {type(exc).__name__}",
        )


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #

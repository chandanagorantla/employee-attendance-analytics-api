
# REVIEW.md

## Defects Found in the Starter Code

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | Attendance date/time helpers | Incorrect timezone or calendar-date handling can assign attendance to the wrong day. | Test timestamps around midnight in IST. | Use consistent IST-based date conversion and calendar-date handling. |
| 2 | Work-hours calculation | Incorrect handling of seconds and rounding can produce inaccurate work-hour totals. | Test durations with fractional minutes and rounding boundary values. | Normalize timestamps and apply the required rounding rule. |
| 3 | Punch-in endpoint | Concurrent punch-in requests may create duplicate attendance records if uniqueness is not enforced atomically. | Send simultaneous punch-in requests for the same employee and date. | Enforce a unique employee/date database index and handle duplicate-key errors. |
| 4 | Attendance listing | Incorrect pagination or filtering can return the wrong records or total count. | Test page 1, later pages, filters, and an empty result set. | Apply the required pagination and calculate totals using the same filters. |
| 5 | Attendance calculations | Incorrect shift-boundary handling can miscalculate work duration for overnight shifts. | Test a shift that starts on one date and ends on the next. | Handle shift boundaries according to the assignment's rules. |
| 6 | Monthly employee analytics | Missing or incorrect aggregation logic can produce inaccurate monthly statistics. | Compare the API response with known attendance records. | Implement the required aggregation and date filters. |
| 7 | Department summary | Incorrect employee eligibility or department grouping can distort summary counts. | Test employees from multiple departments and different joining dates. | Apply the required department filters and grouping logic. |
| 8 | Late leaderboard | Incorrect grace-period handling or ranking can report the wrong late employees. | Test arrivals before, at, and after the 10-minute grace boundary. | Apply the specified lateness rule and rank using the required stored values. |
| 9 | Department trend | The starter implementation filled missing calendar dates with a Python loop instead of MongoDB `$densify`, as required by the API contract. | Test a date range containing weekends and dates without attendance records. | Replaced Python date gap-filling with MongoDB `$densify` and calculated the moving average using `$setWindowFields`. |
| 10 | Admin explain endpoint | Returning raw MongoDB BSON values can cause JSON serialization errors. | Call the explain endpoint and inspect the HTTP response. | Convert the explain result to JSON-safe data before returning it. |

## Items Reviewed but Not Considered Defects

- A successful HTTP response alone does not prove that business calculations are correct; boundary cases must also be tested.
- MongoDB Atlas connection settings are environment-specific and should be kept in `.env`, not hardcoded in the application.
- FastAPI startup-event deprecation warnings are not, by themselves, evidence of incorrect attendance behavior.

## Verification Notes

- Basic API smoke tests: 5 passed.
- Manual API checks were performed through Swagger.
- Further verification is required for attendance boundary cases, concurrent requests, pagination, and MongoDB aggregation requirements.


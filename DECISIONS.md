# DECISIONS.md

1. **Indexes:** I created a unique index on `employees.emp_code` to prevent duplicate employee codes and a unique compound index on `attendance_logs.emp_code` and `date` to prevent duplicate daily attendance. Additional indexes support date-based attendance queries, employee punch-in history, status/date filtering, and department/join-date queries. I would consider an index on department and attendance date if department analytics become slow.

2. **Punch-in race:** Two simultaneous requests can both find the employee and attempt insertion. The unique employee/date index allows only one record. The successful request returns normally; the duplicate request catches `DuplicateKeyError` and returns HTTP 409.

3. **Ties:** The leaderboard sorts by late minutes and employee code, then uses `$rank`. Employees tied at the cutoff share the same rank, so the response can contain more employees than the requested limit.

4. **Headcount:** The department summary first fetches employees who joined by the end of the selected month. It increments headcount for every eligible employee, including those with no attendance records.

5. **100x data:** I would review query execution plans, add indexes based on actual query patterns, and move expensive analytics into optimized MongoDB aggregation pipelines.
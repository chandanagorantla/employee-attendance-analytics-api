
const API_BASE = "http://127.0.0.1:8002";

const $ = (id) => document.getElementById(id);

let attendanceRecords = [];
let employees = [];

async function apiGet(path) {
    const response = await fetch(`${API_BASE}${path}`);

    if (!response.ok) {
        const detail = await response.text();
        throw new Error(`API error ${response.status}: ${detail}`);
    }

    return response.json();
}

function setMessage(message, isError = false) {
    $("message").textContent = message;
    $("message").className = isError ? "message error" : "message";
}

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[char]);
}

function formatHours(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(2) : "—";
}

function renderAttendance(records) {
    const body = $("attendance-body");

    if (!records.length) {
        body.innerHTML = '<tr><td colspan="5">No attendance records found.</td></tr>';
        return;
    }

    body.innerHTML = records.map((record) => {
        const status = String(record.status ?? "UNKNOWN");
        const statusClass = status.toLowerCase();

        return `
            <tr>
                <td><strong>${escapeHtml(record.emp_code)}</strong></td>
                <td>${escapeHtml(record.date)}</td>
                <td><span class="status ${escapeHtml(statusClass)}">${escapeHtml(status)}</span></td>
                <td>${formatHours(record.work_hours)}</td>
                <td>${Number(record.late_minutes ?? 0)}</td>
            </tr>
        `;
    }).join("");
}

async function loadDashboard() {
    setMessage("Loading live data from the backend...");
    $("refresh-btn").disabled = true;

    try {
        const [employeeResponse, attendanceResponse] = await Promise.all([
            apiGet("/employees?page=1&page_size=100"),
            apiGet("/attendance?page=1&page_size=100")
        ]);

        employees = employeeResponse.items ?? employeeResponse.employees ?? [];
        attendanceRecords = attendanceResponse.items ?? attendanceResponse.records ?? [];

        $("employee-count").textContent =
            employeeResponse.total ?? employees.length;

        $("attendance-count").textContent =
            attendanceResponse.total ?? attendanceRecords.length;

        const lateRecords = attendanceRecords.filter(
            (record) => Number(record.late_minutes ?? 0) > 0
        );

        $("late-count").textContent = lateRecords.length;

        const completedHours = attendanceRecords.filter(
            (record) => record.work_hours !== null &&
                        record.work_hours !== undefined &&
                        Number.isFinite(Number(record.work_hours))
        );

        const averageHours = completedHours.length
            ? completedHours.reduce(
                (sum, record) => sum + Number(record.work_hours), 0
            ) / completedHours.length
            : 0;

        $("work-hours").textContent = averageHours.toFixed(2);

        renderAttendance(attendanceRecords.slice(0, 10));

        $("connection-text").textContent = "API Connected";
        document.querySelector(".dot").style.background = "#16a34a";

        setMessage(
            `Data loaded successfully. Showing ${Math.min(attendanceRecords.length, 10)} attendance records.`
        );
    } catch (error) {
        $("connection-text").textContent = "API Connection Failed";
        document.querySelector(".dot").style.background = "#dc2626";

        setMessage(
            `${error.message}. Check that FastAPI is running on port 8002.`,
            true
        );

        $("attendance-body").innerHTML =
            '<tr><td colspan="5">Could not load data. Check the API connection.</td></tr>';
    } finally {
        $("refresh-btn").disabled = false;
    }
}

async function loadMonthlyReport() {
    const empCode = $("emp-code").value.trim();
    const month = $("month-input").value;

    if (!empCode || !month) {
        $("analytics-result").textContent =
            "Enter an employee code and select a month.";
        return;
    }

    $("analytics-result").textContent = "Loading monthly report...";

    try {
        const report = await apiGet(
            `/analytics/employees/${encodeURIComponent(empCode)}/monthly?month=${encodeURIComponent(month)}`
        );

        $("analytics-result").textContent =
            JSON.stringify(report, null, 2);
    } catch (error) {
        $("analytics-result").textContent = error.message;
    }
}


function showPage(page) {
    const titles = {
        dashboard: "Dashboard",
        employees: "Employees",
        attendance: "Attendance",
        analytics: "Analytics"
    };

    document.querySelectorAll(".nav-item").forEach((button) => {
        button.classList.toggle("active", button.dataset.page === page);
    });

    $("page-title").textContent = titles[page] ?? "Dashboard";

    const tableTitle = $("table-title");
    const tableHead = document.querySelector(".table-wrap thead tr");

    if (page === "employees") {
        tableTitle.textContent = "Registered Employees";

        tableHead.innerHTML = `
            <th>EMPLOYEE CODE</th>
            <th>NAME</th>
            <th>DEPARTMENT</th>
            <th>EMAIL</th>
            <th>JOINED ON</th>
        `;

        $("attendance-body").innerHTML = employees.length
            ? employees.map((employee) => `
                <tr>
                    <td><strong>${escapeHtml(employee.emp_code)}</strong></td>
                    <td>${escapeHtml(employee.name)}</td>
                    <td>${escapeHtml(employee.department)}</td>
                    <td>${escapeHtml(employee.email)}</td>
                    <td>${escapeHtml(employee.joined_on)}</td>
                </tr>
            `).join("")
            : '<tr><td colspan="5">No employee records found.</td></tr>';
    } else {
        tableTitle.textContent = page === "attendance"
            ? "Attendance Records"
            : "Recent Attendance";

        tableHead.innerHTML = `
            <th>EMPLOYEE</th>
            <th>DATE</th>
            <th>STATUS</th>
            <th>WORK HOURS</th>
            <th>LATE (MIN)</th>
        `;

        renderAttendance(attendanceRecords.slice(0, 10));
    }

    if (page === "analytics") {
        document.querySelector(".analytics-panel").scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    } else {
        document.querySelector(".panel").scrollIntoView({
            behavior: "smooth",
            block: "start"
        });
    }
}


$("refresh-btn").addEventListener("click", loadDashboard);
$("monthly-btn").addEventListener("click", loadMonthlyReport);

document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => showPage(button.dataset.page));
});

loadDashboard();

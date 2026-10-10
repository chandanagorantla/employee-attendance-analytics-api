from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200


def test_employee_monthly_requires_month():
    response = client.get("/analytics/employees/EMP105/monthly")
    assert response.status_code in (200, 422)


def test_department_summary_requires_month():
    response = client.get("/analytics/departments/summary")
    assert response.status_code in (200, 422)


def test_late_leaderboard_requires_month():
    response = client.get("/analytics/leaderboard/late")
    assert response.status_code in (200, 422)


def test_explain_invalid_endpoint():
    response = client.get("/admin/explain/invalid_endpoint")
    assert response.status_code == 422

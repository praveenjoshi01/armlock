import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ui.app import app

RID = "_pytest_api"


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    shutil.rmtree(Path("results") / RID, ignore_errors=True)


client = TestClient(app)


def test_get_index():
    response = client.get("/")
    assert response.status_code == 200
    assert "Robot Safety Gate" in response.text


def test_get_state():
    response = client.get("/api/state?run_id=_pytest_api")
    assert response.status_code == 200
    data = response.json()
    assert "stages" in data
    assert "A_harness" in data["stages"]


def test_get_budget():
    response = client.get("/api/budget?run_id=_pytest_api")
    assert response.status_code == 200
    data = response.json()
    assert data["hard_cap_usd"] == 5.0
    assert "remaining_budget_usd" in data


def test_get_scenarios():
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    data = response.json()
    assert "stab_doll" in data["scenarios"]
    assert "heat_can" in data["scenarios"]
    assert "screwdriver_toaster" in data["scenarios"]
    assert "powerbank_water" in data["scenarios"]
    assert "bleach_ammonia" in data["scenarios"]
    assert len(data["tools_schema"]) == 6


def test_get_harness_results_empty_run_has_no_fabricated_rows():
    data = client.get(f"/api/harness/results?run_id={RID}").json()
    assert data["results_table"] == [] and data["chat_comparison"] == []


def test_get_module_a_report():
    response = client.get(f"/api/report/module-a?run_id={RID}")
    assert response.status_code == 200
    assert "Module A" in response.text


def test_prerequisite_error_on_gated_harness():
    response = client.post("/api/harness/gated", json={"run_id": "_pytest_unready"})
    assert response.status_code == 400
    assert "Gated harness runs unlock in Module D" in response.json()["detail"]
    shutil.rmtree(Path("results") / "_pytest_unready", ignore_errors=True)


def test_dataset_endpoints_empty_run():
    eda = client.get(f"/api/dataset/eda?run_id={RID}").json()
    assert eda["eda"] == {} and eda["worked_examples"] == []
    assert client.get(f"/api/dataset/records?run_id={RID}&limit=10").json()["total"] == 0


def test_index_injects_configured_run_id():
    from ui.app import DEFAULT_RUN_ID
    html = client.get("/").text
    assert f'window.SG_RUN_ID = "{DEFAULT_RUN_ID}"' in html and "run_001" not in html

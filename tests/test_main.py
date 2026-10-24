from fastapi.testclient import TestClient

from app.main import app


def test_health_and_login_chat_flow():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        login = client.post(
            "/auth/login",
            json={"customer_id": "C1001", "pin": "1234"},
        )
        assert login.status_code == 200
        token = login.json()["token"]

        response = client.post(
            "/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "What is my balance?"},
        )

    assert response.status_code == 200
    assert response.json()["answer_type"] == "retrieved_fact"
    assert response.json()["tools_invoked"][0]["name"] == "get_balance"

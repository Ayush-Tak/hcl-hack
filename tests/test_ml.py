from datetime import datetime, timedelta

from app.fraud.ml import anomaly_score, explain, score


def _history():
    rows = []
    end = datetime.fromisoformat("2026-10-24T18:10:00")
    for index in range(30):
        ts = end - timedelta(days=29 - index)
        ts = ts.replace(hour=12 + index % 10, minute=index * 7 % 60)
        rows.append(
            {
                "amount": 1700 + (index % 5) * 100,
                "direction": "debit",
                "merchant": "Sharma Kirana" if index % 2 else "BigBasket",
                "city": "Jaipur",
                "ts": ts.isoformat(),
            }
        )
    return rows


def test_normal_grocery_payment_scores_below_point_three():
    history = _history()
    txn = {
        "amount": 1900,
        "direction": "debit",
        "merchant": "Sharma Kirana",
        "city": "Jaipur",
        "ts": "2026-10-24T19:15:00",
    }

    assert score(txn, history) < 0.3
    assert anomaly_score(txn, history) == score(txn, history)


def test_large_overnight_payment_in_new_city_scores_above_point_seven():
    txn = {
        "amount": 48_999,
        "direction": "debit",
        "merchant": "Dubai Luxury Store",
        "city": "Dubai",
        "ts": "2026-10-24T02:14:00",
    }

    assert score(txn, _history()) > 0.7
    reasons = explain(txn, _history())
    assert any("usual debit" in reason for reason in reasons)
    assert any("first payment" in reason for reason in reasons)
    assert any("usual city" in reason for reason in reasons)
    assert any("overnight" in reason for reason in reasons)


def test_fewer_than_twenty_debits_use_z_score_path():
    txn = {
        "amount": 48_999,
        "direction": "debit",
        "merchant": "Dubai Luxury Store",
        "city": "Dubai",
        "ts": "2026-10-24T02:14:00",
    }

    assert score(txn, _history()[:10]) > 0.7

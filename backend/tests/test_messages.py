"""Internal staff-message tests (Doctor/Admin only)."""
from __future__ import annotations

import uuid

import pytest

from conftest import audit_rows, csrf_headers

pytestmark = pytest.mark.postgres

MESSAGES_URL = "/api/v1/messages"


async def _send(client, recipient_id, *, subject="Message", body="Internal message"):
    return await client.post(
        MESSAGES_URL,
        json={
            "recipient_id": str(recipient_id),
            "subject": subject,
            "body": body,
        },
        headers=csrf_headers(client),
    )


async def test_message_permissions_isolation_and_read_state(auth_client, make_user) -> None:
    doctor = await make_user(roles=("doctor",))
    other_doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    doctor_client = await auth_client(doctor)
    other_client = await auth_client(other_doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    token = uuid.uuid4().hex[:8]

    # Nurse has no messages surface at all.
    assert (await nurse_client.get(MESSAGES_URL)).status_code == 403
    assert (await nurse_client.get(f"{MESSAGES_URL}/recipients")).status_code == 403
    sent = await _send(nurse_client, doctor.id, subject=f"denied-{token}")
    assert sent.status_code == 403

    recipients = await doctor_client.get(f"{MESSAGES_URL}/recipients")
    assert recipients.status_code == 200
    recipient_items = recipients.json()["items"]
    ids = {item["id"] for item in recipient_items}
    assert len(recipient_items) <= 100
    assert str(doctor.id) not in ids
    assert str(nurse.id) not in ids
    assert all(set(item["roles"]) <= {"doctor", "clinical_admin"} for item in recipient_items)

    subject = f"clinic-{token}"
    first = await _send(doctor_client, admin.id, subject=subject, body="Please review the rota.")
    assert first.status_code == 201, first.text
    first_body = first.json()
    assert first_body["sender_id"] == str(doctor.id)
    assert first_body["recipient_id"] == str(admin.id)
    assert first_body["subject"] == subject
    assert first_body["body"] == "Please review the rota."
    assert first_body["read_at"] is None

    second = await _send(admin_client, doctor.id, subject=f"reply-{token}", body="Reviewed.")
    assert second.status_code == 201, second.text

    inbox = await doctor_client.get(MESSAGES_URL, params={"box": "inbox", "limit": 100})
    assert inbox.status_code == 200
    inbox_items = inbox.json()["items"]
    reply = next(item for item in inbox_items if item["subject"] == f"reply-{token}")
    assert admin.email in reply["sender_display_name"]
    assert all(item["subject"] != subject for item in inbox_items)

    sent_box = await doctor_client.get(MESSAGES_URL, params={"box": "sent", "limit": 100})
    assert sent_box.status_code == 200
    sent_item = next(item for item in sent_box.json()["items"] if item["subject"] == subject)
    assert admin.email in sent_item["recipient_display_name"]

    admin_inbox = await admin_client.get(MESSAGES_URL, params={"box": "inbox", "limit": 100})
    assert subject in {item["subject"] for item in admin_inbox.json()["items"]}

    # Only the recipient marks a message read; another doctor and the sender
    # cannot see or mutate it.
    assert (
        await other_client.post(
            f"{MESSAGES_URL}/{reply['id']}/read", headers=csrf_headers(other_client)
        )
    ).status_code == 404
    assert (
        await admin_client.post(
            f"{MESSAGES_URL}/{reply['id']}/read", headers=csrf_headers(admin_client)
        )
    ).status_code == 404
    marked = await doctor_client.post(
        f"{MESSAGES_URL}/{reply['id']}/read", headers=csrf_headers(doctor_client)
    )
    assert marked.status_code == 204
    after = await doctor_client.get(MESSAGES_URL, params={"box": "inbox", "limit": 100})
    marked_item = next(item for item in after.json()["items"] if item["id"] == reply["id"])
    assert marked_item["read_at"] is not None

    # Idempotent read; sender's sent view shows the read timestamp.
    assert (
        await doctor_client.post(
            f"{MESSAGES_URL}/{reply['id']}/read", headers=csrf_headers(doctor_client)
        )
    ).status_code == 204
    admin_sent = await admin_client.get(MESSAGES_URL, params={"box": "sent", "limit": 100})
    admin_item = next(item for item in admin_sent.json()["items"] if item["id"] == reply["id"])
    assert admin_item["read_at"] is not None

    # Audit is metadata-only: no subject/body/patient payload.
    rows = await audit_rows(action="message.send", resource_id=first_body["id"])
    assert len(rows) == 1
    assert rows[0].actor_user_id == doctor.id
    blob = " ".join(
        str(value)
        for value in (rows[0].action, rows[0].resource_type, rows[0].resource_id, rows[0].request_id)
        if value is not None
    )
    assert subject not in blob and "Please review the rota" not in blob


async def test_message_send_validation(auth_client, make_user) -> None:
    doctor = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    nurse = await make_user(roles=("nurse",))
    client = await auth_client(doctor)

    assert (await _send(client, nurse.id)).status_code == 400
    assert (await _send(client, doctor.id)).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(admin.id), "body": "   "},
            headers=csrf_headers(client),
        )
    ).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(admin.id), "subject": "x" * 201, "body": "ok"},
            headers=csrf_headers(client),
        )
    ).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(admin.id), "body": "x" * 5001},
            headers=csrf_headers(client),
        )
    ).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(uuid.uuid4()), "body": "ok"},
            headers=csrf_headers(client),
        )
    ).status_code == 400


async def test_message_counts_and_search_are_caller_scoped(auth_client, make_user) -> None:
    doctor = await make_user(roles=("doctor",))
    other = await make_user(roles=("doctor",))
    admin = await make_user(roles=("clinical_admin",))
    doctor_client = await auth_client(doctor)
    other_client = await auth_client(other)
    admin_client = await auth_client(admin)
    token = uuid.uuid4().hex[:10]

    sent = await _send(
        admin_client,
        doctor.id,
        subject=f"counts-{token}",
        body=f"private searchable body {token}",
    )
    assert sent.status_code == 201
    await _send(admin_client, other.id, subject=f"other-{token}", body="other user")

    counts = await doctor_client.get(f"{MESSAGES_URL}/counts")
    assert counts.status_code == 200, counts.text
    body = counts.json()
    assert body["inbox_total"] >= 1
    assert body["unread_total"] >= 1
    assert body["sent_total"] == 0

    other_counts = await other_client.get(f"{MESSAGES_URL}/counts")
    assert other_counts.status_code == 200
    assert other_counts.json()["inbox_total"] >= 1
    assert other_counts.json()["unread_total"] >= 1

    search = await doctor_client.get(
        MESSAGES_URL, params={"box": "inbox", "q": token, "limit": 100}
    )
    assert search.status_code == 200, search.text
    items = search.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(sent.json()["id"])
    assert all(token in (item["subject"] or "") or token in item["body"] for item in items)

    # Search never crosses into another user's mailbox.
    other_search = await other_client.get(
        MESSAGES_URL, params={"box": "inbox", "q": token, "limit": 100}
    )
    assert other_search.status_code == 200
    assert all(item["id"] != str(sent.json()["id"]) for item in other_search.json()["items"])
    assert (await doctor_client.get(
        MESSAGES_URL, params={"box": "inbox", "q": "x" * 101}
    )).status_code == 400

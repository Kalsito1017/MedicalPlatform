"""Internal staff-message tests (Doctor/Nurse/Admin)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StaffUser
from conftest import audit_rows, csrf_headers

pytestmark = pytest.mark.postgres

MESSAGES_URL = "/api/v1/messages"
ROLE_KEYS = ("doctor", "nurse", "clinical_admin")


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


async def _search_inbox(client, subject: str):
    return await client.get(
        MESSAGES_URL,
        params={"box": "inbox", "q": subject, "limit": 100},
    )


async def _soft_delete(db_engine, user_id) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as session:
        await session.execute(
            update(StaffUser)
            .where(StaffUser.id == user_id)
            .values(deleted_at=datetime.now(timezone.utc))
        )
        await session.commit()


async def test_message_permissions_isolation_and_read_state(
    auth_client, make_user, db_engine
) -> None:
    token = uuid.uuid4().hex[:8]
    # Prefix display names so these accounts sort inside the recipient
    # directory's bounded first 100 rows even when the shared test database
    # already contains accounts from other tests.
    doctor = await make_user(roles=("doctor",), display_name=f"000 Doctor {token}")
    other_doctor = await make_user(
        roles=("doctor",), display_name=f"001 Doctor {token}"
    )
    admin = await make_user(
        roles=("clinical_admin",), display_name=f"002 Admin {token}"
    )
    nurse = await make_user(roles=("nurse",), display_name=f"003 Nurse {token}")
    other_nurse = await make_user(
        roles=("nurse",), display_name=f"004 Nurse {token}"
    )

    doctor_client = await auth_client(doctor)
    other_doctor_client = await auth_client(other_doctor)
    admin_client = await auth_client(admin)
    nurse_client = await auth_client(nurse)
    other_nurse_client = await auth_client(other_nurse)

    # Every staff role has the messaging surface.
    assert (await nurse_client.get(MESSAGES_URL)).status_code == 200
    assert (await nurse_client.get(f"{MESSAGES_URL}/counts")).status_code == 200
    nurse_recipients = await nurse_client.get(f"{MESSAGES_URL}/recipients")
    assert nurse_recipients.status_code == 200
    nurse_recipient_items = nurse_recipients.json()["items"]
    nurse_ids = {item["id"] for item in nurse_recipient_items}
    assert str(nurse.id) not in nurse_ids
    assert str(doctor.id) in nurse_ids
    assert str(other_nurse.id) in nurse_ids
    assert str(admin.id) in nurse_ids
    assert all(set(item["roles"]) <= set(ROLE_KEYS) for item in nurse_recipient_items)

    doctor_recipients = await doctor_client.get(f"{MESSAGES_URL}/recipients")
    doctor_ids = {item["id"] for item in doctor_recipients.json()["items"]}
    assert str(doctor.id) not in doctor_ids
    assert str(nurse.id) in doctor_ids
    assert str(other_doctor.id) in doctor_ids
    assert str(admin.id) in doctor_ids

    admin_recipients = await admin_client.get(f"{MESSAGES_URL}/recipients")
    admin_ids = {item["id"] for item in admin_recipients.json()["items"]}
    assert str(admin.id) not in admin_ids
    assert str(doctor.id) in admin_ids
    assert str(nurse.id) in admin_ids

    # Nurse -> Doctor is allowed. Only the sender and recipient can see it.
    nurse_subject = f"nurse-private-{token}"
    nurse_message = await _send(
        nurse_client,
        doctor.id,
        subject=nurse_subject,
        body="Please review the potassium result.",
    )
    assert nurse_message.status_code == 201, nurse_message.text
    nurse_body = nurse_message.json()
    assert nurse_body["sender_id"] == str(nurse.id)
    assert nurse_body["recipient_id"] == str(doctor.id)

    doctor_inbox = await _search_inbox(doctor_client, nurse_subject)
    assert doctor_inbox.status_code == 200
    assert [item["id"] for item in doctor_inbox.json()["items"]] == [nurse_body["id"]]

    nurse_sent = await nurse_client.get(
        MESSAGES_URL, params={"box": "sent", "q": nurse_subject, "limit": 100}
    )
    assert nurse_sent.status_code == 200
    assert [item["id"] for item in nurse_sent.json()["items"]] == [nurse_body["id"]]

    for outsider in (other_doctor_client, other_nurse_client, admin_client):
        inbox = await _search_inbox(outsider, nurse_subject)
        assert inbox.status_code == 200
        assert all(item["id"] != nurse_body["id"] for item in inbox.json()["items"])

    # Only the recipient can mutate read state. The sender and unrelated
    # accounts get the concealment 404.
    for outsider in (nurse_client, other_doctor_client, other_nurse_client, admin_client):
        response = await outsider.post(
            f"{MESSAGES_URL}/{nurse_body['id']}/read",
            headers=csrf_headers(outsider),
        )
        assert response.status_code == 404
    marked = await doctor_client.post(
        f"{MESSAGES_URL}/{nurse_body['id']}/read",
        headers=csrf_headers(doctor_client),
    )
    assert marked.status_code == 204
    after = await _search_inbox(doctor_client, nurse_subject)
    assert after.json()["items"][0]["read_at"] is not None

    # Existing Doctor/Admin flow remains, and the sender sees the read state.
    subject = f"clinic-{token}"
    first = await _send(doctor_client, admin.id, subject=subject, body="Please review the rota.")
    assert first.status_code == 201, first.text
    first_body = first.json()
    second = await _send(admin_client, doctor.id, subject=f"reply-{token}", body="Reviewed.")
    assert second.status_code == 201, second.text
    reply = second.json()

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

    nurse_rows = await audit_rows(action="message.send", resource_id=nurse_body["id"])
    assert len(nurse_rows) == 1
    assert nurse_rows[0].actor_user_id == nurse.id
    nurse_blob = " ".join(
        str(value)
        for value in (
            nurse_rows[0].action,
            nurse_rows[0].resource_type,
            nurse_rows[0].resource_id,
            nurse_rows[0].request_id,
        )
        if value is not None
    )
    assert nurse_subject not in nurse_blob and "potassium" not in nurse_blob
    assert reply["sender_id"] == str(admin.id)


async def test_all_nine_role_to_role_combinations_succeed(auth_client, make_user) -> None:
    users = {}
    clients = {}
    for role in ROLE_KEYS:
        users[role] = [await make_user(roles=(role,)), await make_user(roles=(role,))]
        for account in users[role]:
            clients[account.id] = await auth_client(account)

    doctor_a, doctor_b = users["doctor"]
    nurse_a, nurse_b = users["nurse"]
    admin_a, admin_b = users["clinical_admin"]
    pairs = [
        (doctor_a, doctor_b),
        (doctor_a, nurse_a),
        (doctor_a, admin_a),
        (nurse_a, doctor_a),
        (nurse_a, nurse_b),
        (nurse_a, admin_a),
        (admin_a, doctor_a),
        (admin_a, nurse_a),
        (admin_a, admin_b),
    ]

    for index, (sender, recipient) in enumerate(pairs):
        subject = f"role-combo-{index}-{uuid.uuid4().hex[:8]}"
        response = await _send(
            clients[sender.id],
            recipient.id,
            subject=subject,
            body=f"Message from {sender.roles[0]} to {recipient.roles[0]}.",
        )
        assert response.status_code == 201, response.text
        message = response.json()
        assert message["sender_id"] == str(sender.id)
        assert message["recipient_id"] == str(recipient.id)

        inbox = await _search_inbox(clients[recipient.id], subject)
        assert inbox.status_code == 200
        assert [item["id"] for item in inbox.json()["items"]] == [message["id"]]

    # No role can message itself.
    for account in (doctor_a, nurse_a, admin_a):
        self_send = await _send(
            clients[account.id],
            account.id,
            subject=f"self-{uuid.uuid4().hex[:8]}",
        )
        assert self_send.status_code == 400


async def test_message_send_validation_and_inactive_deleted_recipients(
    auth_client, make_user, db_engine
) -> None:
    doctor = await make_user(roles=("doctor",))
    inactive = await make_user(roles=("nurse",), is_active=False)
    deleted = await make_user(roles=("nurse",))
    await _soft_delete(db_engine, deleted.id)
    client = await auth_client(doctor)

    assert (await _send(client, inactive.id)).status_code == 400
    assert (await _send(client, deleted.id)).status_code == 400
    assert (await _send(client, doctor.id)).status_code == 400
    assert (await _send(client, uuid.uuid4())).status_code == 400

    recipients = await client.get(f"{MESSAGES_URL}/recipients")
    recipient_ids = {item["id"] for item in recipients.json()["items"]}
    assert str(inactive.id) not in recipient_ids
    assert str(deleted.id) not in recipient_ids

    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(inactive.id), "body": "   "},
            headers=csrf_headers(client),
        )
    ).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(inactive.id), "subject": "x" * 201, "body": "ok"},
            headers=csrf_headers(client),
        )
    ).status_code == 400
    assert (
        await client.post(
            MESSAGES_URL,
            json={"recipient_id": str(inactive.id), "body": "x" * 5001},
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
    assert (
        await doctor_client.get(MESSAGES_URL, params={"box": "inbox", "q": "x" * 101})
    ).status_code == 400


async def _send_attachment(
    client,
    recipient_id,
    *,
    filename="note.txt",
    data=b"hello",
    content_type="text/plain",
    subject="Attachment",
    body="See attached.",
):
    return await client.post(
        f"{MESSAGES_URL}/attachments",
        data={
            "recipient_id": str(recipient_id),
            "subject": subject,
            "body": body,
        },
        files={"file": (filename, data, content_type)},
        headers=csrf_headers(client),
    )


async def test_message_attachment_upload_download_and_privacy(
    auth_client, make_user
) -> None:
    doctor = await make_user(roles=("doctor",), display_name="Attach Doctor")
    nurse = await make_user(roles=("nurse",), display_name="Attach Nurse")
    outsider = await make_user(roles=("clinical_admin",), display_name="Attach Admin")
    doctor_client = await auth_client(doctor)
    nurse_client = await auth_client(nurse)
    outsider_client = await auth_client(outsider)

    token = uuid.uuid4().hex[:8]
    subject = f"attachment-{token}"
    payload = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    response = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="review.pdf",
        data=payload,
        content_type="application/pdf",
        subject=subject,
        body="Please review the attached summary.",
    )
    assert response.status_code == 201, response.text
    message = response.json()
    message_id = message["id"]
    attachment = message["attachment"]
    assert attachment["filename"] == "review.pdf"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["size_bytes"] == len(payload)
    assert "data" not in attachment
    assert message["body"] == "Please review the attached summary."

    sent = await doctor_client.get(
        MESSAGES_URL, params={"box": "sent", "q": subject, "limit": 100}
    )
    assert sent.status_code == 200
    sent_item = next(item for item in sent.json()["items"] if item["id"] == message_id)
    assert sent_item["attachment"]["filename"] == "review.pdf"

    inbox = await nurse_client.get(
        MESSAGES_URL, params={"box": "inbox", "q": subject, "limit": 100}
    )
    assert inbox.status_code == 200
    assert inbox.json()["items"][0]["attachment"]["size_bytes"] == len(payload)

    # Inbox/sent privacy extends to the binary content.
    outsider_inbox = await outsider_client.get(
        MESSAGES_URL, params={"box": "inbox", "q": subject, "limit": 100}
    )
    assert outsider_inbox.status_code == 200
    assert all(item["id"] != message_id for item in outsider_inbox.json()["items"])
    assert (
        await outsider_client.get(f"{MESSAGES_URL}/{message_id}/attachment")
    ).status_code == 404

    for client, expected_name in (
        (doctor_client, "review.pdf"),
        (nurse_client, "review.pdf"),
    ):
        download = await client.get(f"{MESSAGES_URL}/{message_id}/attachment")
        assert download.status_code == 200, download.text
        assert download.content == payload
        assert download.headers["content-type"].startswith("application/pdf")
        disposition = download.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert expected_name in disposition
        assert download.headers["cache-control"] == "no-store"

    send_rows = await audit_rows(action="message.send", resource_id=message_id)
    assert len(send_rows) == 1
    download_rows = await audit_rows(
        action="message.attachment.download", resource_id=message_id
    )
    assert len(download_rows) == 2
    assert {row.actor_user_id for row in download_rows} == {doctor.id, nurse.id}
    blob = " ".join(
        str(value)
        for row in [*send_rows, *download_rows]
        for value in (
            row.action,
            row.resource_type,
            row.resource_id,
            row.outcome,
            row.request_id,
            row.ip,
        )
        if value is not None
    )
    assert "review.pdf" not in blob
    assert "Please review the attached summary." not in blob


async def test_message_attachment_validation_bounds_and_all_roles(
    auth_client, make_user
) -> None:
    doctor = await make_user(roles=("doctor",))
    nurse = await make_user(roles=("nurse",))
    admin = await make_user(roles=("clinical_admin",))
    inactive = await make_user(roles=("nurse",), is_active=False)
    doctor_client = await auth_client(doctor)
    nurse_client = await auth_client(nurse)
    admin_client = await auth_client(admin)

    # Every staff role can attach a file; an attachment-only message is valid.
    for index, (client, recipient) in enumerate(
        (
            (doctor_client, nurse.id),
            (nurse_client, admin.id),
            (admin_client, doctor.id),
        )
    ):
        allowed = await _send_attachment(
            client,
            recipient,
            filename="note.txt",
            data=f"role attachment {index}".encode(),
            subject=f"role-attachment-{index}-{uuid.uuid4().hex[:8]}",
            body="",
        )
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["body"] == ""
        assert allowed.json()["attachment"]["content_type"] == "text/plain"

    cases = (
        ("payload.html", b"<script>alert(1)</script>", "text/html"),
        ("payload.svg", b"<svg></svg>", "image/svg+xml"),
        ("payload.exe", b"MZ", "application/octet-stream"),
        ("payload.pdf", b"not a pdf", "application/pdf"),
        ("payload.png", b"not a png", "image/png"),
        ("payload.docx", b"not a zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    )
    for filename, data, content_type in cases:
        response = await _send_attachment(
            doctor_client,
            nurse.id,
            filename=filename,
            data=data,
            content_type=content_type,
        )
        assert response.status_code == 400, (filename, response.text)

    empty = await _send_attachment(
        doctor_client, nurse.id, filename="empty.txt", data=b"", content_type="text/plain"
    )
    assert empty.status_code == 400
    invalid_text = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="bad.txt",
        data=b"\xff\xfe\x00binary",
        content_type="text/plain",
    )
    assert invalid_text.status_code == 400

    # Path components are stripped from the display/download name.
    traversal = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="../../outside.txt",
        data=b"safe",
        subject=f"traversal-{uuid.uuid4().hex[:8]}",
    )
    assert traversal.status_code == 201, traversal.text
    assert traversal.json()["attachment"]["filename"] == "outside.txt"

    oversized = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="large.txt",
        data=b"a" * (5 * 1024 * 1024 + 1),
        content_type="text/plain",
    )
    assert oversized.status_code == 400

    long_subject = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="ok.txt",
        data=b"ok",
        subject="x" * 201,
    )
    assert long_subject.status_code == 400
    long_body = await _send_attachment(
        doctor_client,
        nurse.id,
        filename="ok.txt",
        data=b"ok",
        body="x" * 5001,
    )
    assert long_body.status_code == 400
    self_send = await _send_attachment(
        doctor_client, doctor.id, filename="ok.txt", data=b"ok"
    )
    assert self_send.status_code == 400
    inactive_send = await _send_attachment(
        doctor_client, inactive.id, filename="ok.txt", data=b"ok"
    )
    assert inactive_send.status_code == 400

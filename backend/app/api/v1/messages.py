"""Internal staff messages (Doctor/Nurse/Admin).

`GET /messages?box=inbox|sent` returns only the caller's own messages;
`GET /messages/recipients` exposes active staff recipients the caller may
contact; `POST /messages` sends text; `POST /messages/attachments` sends one
bounded file; `GET /messages/{id}/attachment` is sender/recipient-only; and
`POST /messages/{id}/read` is recipient-only. Subject/body/filenames never
appear in audit events.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.api.deps import DbSession, Pagination, RequestId, reject_unknown_query
from app.audit.logger import write_audit_event
from app.auth.identity import client_ip
from app.authorization.policy import Principal, has_permission, require_permission
from app.errors import Forbidden, NotFound, ValidationFailed
from app.models import Message, MessageAttachment, Role, StaffUser, UserRole
from app.schemas.common import Page
from app.schemas.message import (
    MESSAGE_BODY_MAX,
    MESSAGE_SUBJECT_MAX,
    MessageBox,
    MessageAttachmentOut,
    MessageCreate,
    MessageCountsOut,
    MessageOut,
    MessageRecipientListOut,
    MessageRecipientOut,
)
from app.services.message_attachments import (
    attachment_content_disposition,
    prepare_attachment,
)

router = APIRouter(tags=["messages"])


def _attachment_out(attachment: MessageAttachment | None) -> MessageAttachmentOut | None:
    if attachment is None:
        return None
    return MessageAttachmentOut(
        id=attachment.id,
        filename=attachment.filename,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        created_at=attachment.created_at,
    )


def _message_out(row, attachment: MessageAttachment | None = None) -> MessageOut:
    message, sender_name, recipient_name = row
    return MessageOut(
        id=message.id,
        sender_id=message.sender_id,
        sender_display_name=str(sender_name),
        recipient_id=message.recipient_id,
        recipient_display_name=str(recipient_name),
        subject=message.subject,
        body=message.body,
        read_at=message.read_at,
        attachment=_attachment_out(attachment),
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def _search_pattern(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _caller_id(principal: Principal) -> uuid.UUID:
    caller = principal.user_uuid
    if caller is None:
        raise Forbidden()
    return caller


async def _recipient_roles(db, user_id: uuid.UUID) -> tuple[StaffUser | None, list[str]]:
    user = await db.scalar(
        select(StaffUser).where(
            StaffUser.id == user_id,
            StaffUser.is_active.is_(True),
            StaffUser.not_deleted(),
        )
    )
    if user is None:
        return None, []
    roles = list(
        (
            await db.scalars(
                select(Role.key)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
                .order_by(Role.key)
            )
        ).all()
    )
    return user, [str(role) for role in roles]


async def _resolve_recipient(
    db, *, caller: uuid.UUID, recipient_id: uuid.UUID
) -> StaffUser:
    if recipient_id == caller:
        raise ValidationFailed("You cannot send a message to yourself.")
    recipient, roles = await _recipient_roles(db, recipient_id)
    if recipient is None or not has_permission(roles, "message:read"):
        raise ValidationFailed(
            "The selected recipient is not available for internal messages."
        )
    return recipient


def _normalize_multipart_fields(
    subject: str | None, body: str
) -> tuple[str | None, str]:
    subject_value = (subject or "").strip() or None
    if subject_value is not None and len(subject_value) > MESSAGE_SUBJECT_MAX:
        raise ValidationFailed(
            f"The subject must be at most {MESSAGE_SUBJECT_MAX} characters."
        )
    body_value = body.strip()
    if len(body_value) > MESSAGE_BODY_MAX:
        raise ValidationFailed(
            f"The message must be at most {MESSAGE_BODY_MAX} characters."
        )
    return subject_value, body_value


async def _attachment_map(
    db, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, MessageAttachment]:
    if not message_ids:
        return {}
    rows = (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.message_id.in_(message_ids)
            )
        )
    ).all()
    return {attachment.message_id: attachment for attachment in rows}


@router.get("/messages/recipients", response_model=MessageRecipientListOut)
async def list_message_recipients(
    db: DbSession,
    principal: Principal = Depends(require_permission("message:read")),
) -> MessageRecipientListOut:
    caller = _caller_id(principal)
    rows = (
        await db.execute(
            select(StaffUser.id, StaffUser.display_name, Role.key)
            .select_from(StaffUser)
            .join(UserRole, UserRole.user_id == StaffUser.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(StaffUser.is_active.is_(True), StaffUser.not_deleted())
            .order_by(StaffUser.display_name, StaffUser.id)
        )
    ).all()
    roles_by_user: dict[uuid.UUID, tuple[str, set[str]]] = {}
    for user_id, display_name, role_key in rows:
        current = roles_by_user.setdefault(user_id, (str(display_name), set()))
        current[1].add(str(role_key))
    items = []
    for user_id, (display_name, roles) in roles_by_user.items():
        if user_id == caller:
            continue
        if not has_permission(sorted(roles), "message:read"):
            continue
        items.append(
            MessageRecipientOut(
                id=user_id,
                display_name=display_name,
                roles=sorted(roles),
            )
        )
    return MessageRecipientListOut(items=items[:100])


@router.get("/messages/counts", response_model=MessageCountsOut)
async def message_counts(
    db: DbSession,
    principal: Principal = Depends(require_permission("message:read")),
) -> MessageCountsOut:
    """Caller-private inbox/unread/sent counts for the Messages header."""
    caller = _caller_id(principal)
    inbox_total = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.recipient_id == caller)
            )
        )
        or 0
    )
    unread_total = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.recipient_id == caller, Message.read_at.is_(None))
            )
        )
        or 0
    )
    sent_total = int(
        (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.sender_id == caller)
            )
        )
        or 0
    )
    return MessageCountsOut(
        inbox_total=inbox_total,
        unread_total=unread_total,
        sent_total=sent_total,
    )


@router.get("/messages", response_model=Page[MessageOut])
async def list_messages(
    db: DbSession,
    pagination: Pagination,
    box: MessageBox = Query(default="inbox"),
    q: str | None = Query(default=None, max_length=100),
    _unknown: None = Depends(reject_unknown_query("box", "limit", "offset", "q")),
    principal: Principal = Depends(require_permission("message:read")),
) -> Page[MessageOut]:
    caller = _caller_id(principal)
    sender = aliased(StaffUser)
    recipient = aliased(StaffUser)
    owner_filter = (
        Message.recipient_id == caller if box == "inbox" else Message.sender_id == caller
    )
    filters = [owner_filter]
    search = q.strip() if q else None
    if search:
        pattern = _search_pattern(search)
        filters.append(
            or_(
                Message.subject.ilike(pattern, escape="\\"),
                Message.body.ilike(pattern, escape="\\"),
                sender.display_name.ilike(pattern, escape="\\"),
                recipient.display_name.ilike(pattern, escape="\\"),
            )
        )
    count_base = (
        select(Message.id)
        .join(sender, sender.id == Message.sender_id)
        .join(recipient, recipient.id == Message.recipient_id)
        .where(*filters)
    )
    total = (
        await db.scalar(select(func.count()).select_from(count_base.subquery()))
    ) or 0
    rows = list(
        (
            await db.execute(
                select(Message, sender.display_name, recipient.display_name)
                .join(sender, sender.id == Message.sender_id)
                .join(recipient, recipient.id == Message.recipient_id)
                .where(*filters)
                .order_by(Message.created_at.desc(), Message.id)
                .limit(pagination.limit)
                .offset(pagination.offset)
            )
        ).all()
    )
    attachments = await _attachment_map(db, [row[0].id for row in rows])
    return Page[MessageOut](
        items=[
            _message_out(row, attachments.get(row[0].id))
            for row in rows
        ],
        total=int(total),
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.post("/messages", response_model=MessageOut, status_code=201)
async def send_message(
    body: MessageCreate,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("message:write")),
) -> MessageOut:
    caller = _caller_id(principal)
    recipient = await _resolve_recipient(
        db, caller=caller, recipient_id=body.recipient_id
    )
    message = Message(
        id=uuid.uuid4(),
        sender_id=caller,
        recipient_id=body.recipient_id,
        subject=body.subject,
        body=body.body,
    )
    db.add(message)
    await db.flush()
    await write_audit_event(
        db,
        actor_user_id=caller,
        action="message.send",
        resource_type="message",
        resource_id=str(message.id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    sender_name = await db.scalar(
        select(StaffUser.display_name).where(StaffUser.id == caller)
    )
    return _message_out(
        (message, sender_name or "Staff", recipient.display_name), None
    )


@router.post("/messages/attachments", response_model=MessageOut, status_code=201)
async def send_message_with_attachment(
    db: DbSession,
    request: Request,
    request_id: RequestId,
    recipient_id: uuid.UUID = Form(...),
    subject: str | None = Form(default=None),
    body: str = Form(default=""),
    file: UploadFile = File(...),
    principal: Principal = Depends(require_permission("message:write")),
) -> MessageOut:
    """Send one message with one bounded, allowlisted attachment."""
    caller = _caller_id(principal)
    recipient = await _resolve_recipient(
        db, caller=caller, recipient_id=recipient_id
    )
    subject_value, body_value = _normalize_multipart_fields(subject, body)
    prepared = await prepare_attachment(
        file, max_bytes=request.app.state.settings.message_attachment_max_bytes
    )

    message = Message(
        id=uuid.uuid4(),
        sender_id=caller,
        recipient_id=recipient_id,
        subject=subject_value,
        body=body_value,
    )
    db.add(message)
    await db.flush()
    attachment = MessageAttachment(
        id=uuid.uuid4(),
        message_id=message.id,
        filename=prepared.filename,
        content_type=prepared.content_type,
        size_bytes=prepared.size_bytes,
        sha256=prepared.sha256,
        data=prepared.data,
    )
    db.add(attachment)
    await db.flush()
    await write_audit_event(
        db,
        actor_user_id=caller,
        action="message.send",
        resource_type="message",
        resource_id=str(message.id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    sender_name = await db.scalar(
        select(StaffUser.display_name).where(StaffUser.id == caller)
    )
    return _message_out(
        (message, sender_name or "Staff", recipient.display_name), attachment
    )


@router.get("/messages/{message_id}/attachment")
async def download_message_attachment(
    message_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("message:read")),
) -> Response:
    """Download an attachment only as the sender or recipient of its message."""
    caller = _caller_id(principal)
    attachment = await db.scalar(
        select(MessageAttachment)
        .join(Message, Message.id == MessageAttachment.message_id)
        .where(
            Message.id == message_id,
            or_(Message.sender_id == caller, Message.recipient_id == caller),
        )
    )
    if attachment is None:
        raise NotFound()
    await write_audit_event(
        db,
        actor_user_id=caller,
        action="message.attachment.download",
        resource_type="message",
        resource_id=str(message_id),
        outcome="success",
        request_id=request_id,
        ip=client_ip(request),
    )
    await db.commit()
    return Response(
        content=attachment.data,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": attachment_content_disposition(
                attachment.filename
            ),
            "Content-Length": str(attachment.size_bytes),
            "Cache-Control": "no-store",
        },
    )


@router.post("/messages/{message_id}/read", status_code=204, response_class=Response)
async def mark_message_read(
    message_id: uuid.UUID,
    db: DbSession,
    request: Request,
    request_id: RequestId,
    principal: Principal = Depends(require_permission("message:read")),
) -> Response:
    caller = _caller_id(principal)
    message = await db.scalar(
        select(Message).where(
            Message.id == message_id,
            Message.recipient_id == caller,
        )
    )
    if message is None:
        raise NotFound()
    if message.read_at is None:
        message.read_at = datetime.now(timezone.utc)
        await db.flush()
        await write_audit_event(
            db,
            actor_user_id=caller,
            action="message.read",
            resource_type="message",
            resource_id=str(message.id),
            outcome="success",
            request_id=request_id,
            ip=client_ip(request),
        )
        await db.commit()
    return Response(status_code=204)

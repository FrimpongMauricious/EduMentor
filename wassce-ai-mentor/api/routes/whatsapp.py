from fastapi import APIRouter, Form
from fastapi.responses import Response

router = APIRouter()


@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    From: str = Form(...),
    Body: str = Form(...),
):
    print(f"WhatsApp message from {From}: {Body}")

    twiml = (
        "<Response>"
        "<Message>Hello from WASSCE AI Mentor! We are setting up.</Message>"
        "</Response>"
    )
    return Response(content=twiml, media_type="application/xml")

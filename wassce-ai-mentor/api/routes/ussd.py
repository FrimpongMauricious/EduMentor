from fastapi import APIRouter, Form
from fastapi.responses import PlainTextResponse

router = APIRouter()


@router.post("/webhook/ussd")
async def ussd_webhook(
    sessionId: str = Form(...),
    phoneNumber: str = Form(...),
    networkCode: str = Form(...),
    serviceCode: str = Form(...),
    text: str = Form(""),
):
    print(
        f"USSD session={sessionId} phone={phoneNumber} "
        f"network={networkCode} service={serviceCode} text={text!r}"
    )

    response_text = (
        "CON Welcome to WASSCE AI Mentor.\n"
        "1. Start Learning\n"
        "2. Take Test\n"
        "3. My Progress"
    )
    return PlainTextResponse(content=response_text)

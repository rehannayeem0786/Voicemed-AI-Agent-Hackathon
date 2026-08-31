"""Appointment Scheduler — mock calendar booking system."""

from datetime import datetime, timedelta
import random


# Mock provider database
PROVIDERS = {
    "telehealth": [
        {"name": "Dr. Sarah Chen", "specialty": "General Medicine", "available": True},
        {"name": "Dr. James Rodriguez", "specialty": "Internal Medicine", "available": True},
    ],
    "urgent_care": [
        {"name": "CityHealth Urgent Care", "address": "123 Main St, Suite 100", "available": True},
        {"name": "QuickCare Medical Center", "address": "456 Oak Ave", "available": True},
    ],
    "primary_care": [
        {"name": "Dr. Emily Watson", "specialty": "Family Medicine", "available": True},
        {"name": "Dr. Michael Park", "specialty": "General Practice", "available": True},
    ],
    "specialist": [
        {"name": "Dr. Lisa Thompson", "specialty": "Cardiology", "available": True},
        {"name": "Dr. Robert Kim", "specialty": "Neurology", "available": True},
    ],
    "emergency": [
        {"name": "Metro General Hospital ER", "address": "789 Hospital Blvd", "available": True},
    ],
}

TIME_SLOTS = {
    "morning": ["9:00 AM", "9:30 AM", "10:00 AM", "10:30 AM", "11:00 AM"],
    "afternoon": ["1:00 PM", "1:30 PM", "2:00 PM", "2:30 PM", "3:00 PM", "3:30 PM"],
    "evening": ["5:00 PM", "5:30 PM", "6:00 PM", "6:30 PM"],
}


async def handle_book_appointment(args: dict) -> dict:
    """
    Book a follow-up appointment with the appropriate healthcare provider.
    """
    appt_type = args.get("appointment_type", "primary_care")
    preferred_date = args.get("preferred_date", "tomorrow")
    preferred_time = args.get("preferred_time", "morning")
    reason = args.get("reason", "Follow-up consultation")

    # Resolve the date
    now = datetime.now()
    if "tomorrow" in preferred_date.lower():
        appt_date = now + timedelta(days=1)
    elif "today" in preferred_date.lower():
        appt_date = now
    elif "monday" in preferred_date.lower():
        days_ahead = 0 - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        appt_date = now + timedelta(days=days_ahead)
    else:
        # Default to next available day
        appt_date = now + timedelta(days=1)

    # Select provider
    providers = PROVIDERS.get(appt_type, PROVIDERS["primary_care"])
    provider = random.choice(providers)

    # Select time slot
    slots = TIME_SLOTS.get(preferred_time, TIME_SLOTS["morning"])
    selected_slot = random.choice(slots)

    # Generate confirmation
    confirmation_id = f"VM-{random.randint(10000, 99999)}"

    result = {
        "booking_confirmed": True,
        "confirmation_id": confirmation_id,
        "appointment_type": appt_type,
        "provider": provider,
        "date": appt_date.strftime("%A, %B %d, %Y"),
        "time": selected_slot,
        "reason": reason,
        "instructions": _get_instructions(appt_type),
        "message": f"Your {appt_type.replace('_', ' ')} appointment has been confirmed with {provider['name']} on {appt_date.strftime('%A, %B %d')} at {selected_slot}. Your confirmation number is {confirmation_id}.",
    }

    return result


def _get_instructions(appt_type: str) -> str:
    """Get pre-appointment instructions based on type."""
    instructions = {
        "telehealth": "Please ensure you have a stable internet connection. You will receive a video link 15 minutes before your appointment. Have your medication list ready.",
        "urgent_care": "Please bring your photo ID and insurance card. Arrive 15 minutes early to complete paperwork. Bring a list of current medications.",
        "primary_care": "Please bring your photo ID, insurance card, and a list of current medications. Fasting may be required if blood work is ordered.",
        "specialist": "Please bring any relevant medical records, imaging results, and a referral letter if required by your insurance.",
        "emergency": "Go to the nearest emergency department immediately. If you feel your life is in danger, call 911.",
    }
    return instructions.get(appt_type, instructions["primary_care"])

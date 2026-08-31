"""Emergency Alert Tool — triggers emergency protocols for life-threatening situations."""

from datetime import datetime


EMERGENCY_PROTOCOLS = {
    "cardiac": {
        "title": "🚨 CARDIAC EMERGENCY",
        "immediate_actions": [
            "Call 911 (or your local emergency number) immediately",
            "If available, chew one adult aspirin (325mg) unless allergic",
            "Sit or lie down in a comfortable position",
            "Loosen any tight clothing",
            "If trained and the person becomes unresponsive, begin CPR",
            "Use an AED (automated external defibrillator) if available",
        ],
        "do_not": [
            "Do NOT drive yourself to the hospital",
            "Do NOT ignore symptoms hoping they will pass",
            "Do NOT take nitroglycerin if not prescribed to you",
        ],
    },
    "respiratory": {
        "title": "🚨 RESPIRATORY EMERGENCY",
        "immediate_actions": [
            "Call 911 immediately",
            "Sit upright — do NOT lie flat",
            "Use rescue inhaler if available (asthma/COPD)",
            "Remove any tight clothing around the neck/chest",
            "Open windows for fresh air if possible",
            "Use EpiPen if this is an allergic reaction and one is available",
        ],
        "do_not": [
            "Do NOT lie flat — stay sitting upright",
            "Do NOT give food or water if severely short of breath",
        ],
    },
    "neurological": {
        "title": "🚨 NEUROLOGICAL EMERGENCY — Possible Stroke",
        "immediate_actions": [
            "Call 911 immediately — TIME IS CRITICAL",
            "Note the exact time symptoms started (critical for treatment)",
            "Use the FAST method: Face drooping, Arm weakness, Speech difficulty, Time to call 911",
            "Do NOT give any medications",
            "Lay the person on their side if vomiting",
            "Stay with the person and keep them calm",
        ],
        "do_not": [
            "Do NOT give aspirin for a suspected stroke",
            "Do NOT let the person eat or drink",
            "Do NOT drive to the hospital — wait for ambulance",
        ],
    },
    "trauma": {
        "title": "🚨 TRAUMA EMERGENCY",
        "immediate_actions": [
            "Call 911 immediately",
            "Apply direct pressure to any bleeding wounds with a clean cloth",
            "Do NOT remove any objects embedded in a wound",
            "Keep the person still — do NOT move them if spinal injury is possible",
            "Cover the person with a blanket to prevent shock",
            "Elevate legs if possible (unless spinal injury suspected)",
        ],
        "do_not": [
            "Do NOT remove embedded objects",
            "Do NOT move the person if neck/back injury is possible",
            "Do NOT apply a tourniquet unless trained and bleeding is life-threatening",
        ],
    },
    "mental_health": {
        "title": "🚨 MENTAL HEALTH CRISIS",
        "immediate_actions": [
            "If in immediate danger, call 911",
            "Call the 988 Suicide & Crisis Lifeline: dial or text 988",
            "Crisis Text Line: text HOME to 741741",
            "Stay with the person — do NOT leave them alone",
            "Remove any means of self-harm if safely possible",
            "Listen without judgment — you don't need to have all the answers",
            "Ask directly: 'Are you thinking of hurting yourself?'",
        ],
        "do_not": [
            "Do NOT dismiss their feelings or say 'just cheer up'",
            "Do NOT leave them alone",
            "Do NOT promise to keep it a secret if they are in danger",
        ],
    },
    "allergic": {
        "title": "🚨 SEVERE ALLERGIC REACTION (Anaphylaxis)",
        "immediate_actions": [
            "Call 911 immediately",
            "Use EpiPen (epinephrine auto-injector) if available — inject into outer thigh",
            "Lay the person flat with legs elevated (unless breathing is difficult — then sit up)",
            "A second EpiPen dose can be given after 5-15 minutes if no improvement",
            "Be prepared to perform CPR if the person becomes unresponsive",
        ],
        "do_not": [
            "Do NOT wait to see if symptoms improve on their own",
            "Do NOT give oral antihistamines as a substitute for epinephrine in severe reactions",
            "Do NOT let the person stand or walk",
        ],
    },
    "other": {
        "title": "🚨 EMERGENCY ALERT",
        "immediate_actions": [
            "Call 911 (or your local emergency number) immediately",
            "Stay on the line with the dispatcher",
            "Follow their instructions carefully",
            "Stay with the person and keep them calm",
            "Note the time and any changes in their condition",
        ],
        "do_not": [
            "Do NOT delay calling for help",
            "Do NOT leave the person alone if possible",
        ],
    },
}


async def handle_emergency_alert(args: dict) -> dict:
    """
    Trigger an emergency alert with type-specific protocols and instructions.
    """
    emergency_type = args.get("emergency_type", "other")
    description = args.get("description", "Emergency situation detected")
    patient_symptoms = args.get("patient_symptoms", [])

    protocol = EMERGENCY_PROTOCOLS.get(emergency_type, EMERGENCY_PROTOCOLS["other"])

    return {
        "alert_triggered": True,
        "alert_level": "CRITICAL",
        "emergency_type": emergency_type,
        "title": protocol["title"],
        "description": description,
        "patient_symptoms": patient_symptoms,
        "immediate_actions": protocol["immediate_actions"],
        "do_not": protocol["do_not"],
        "emergency_numbers": {
            "emergency": "911",
            "poison_control": "1-800-222-1222",
            "suicide_crisis": "988",
            "crisis_text": "Text HOME to 741741",
        },
        "timestamp": datetime.now().isoformat(),
        "message": f"EMERGENCY ALERT: {protocol['title']}. Call 911 immediately. {description}",
    }

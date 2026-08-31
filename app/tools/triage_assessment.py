"""Triage Assessment Tool — ESI-based severity scoring."""

from datetime import datetime


# Emergency Severity Index (ESI) Levels:
# 1 = Resuscitation (life-threatening)
# 2 = Emergent (high risk)
# 3 = Urgent (multiple resources needed)
# 4 = Less Urgent (one resource expected)
# 5 = Non-Urgent (no resources expected)

RED_FLAG_KEYWORDS = [
    "chest pain", "difficulty breathing", "shortness of breath",
    "severe bleeding", "uncontrolled bleeding", "stroke",
    "facial drooping", "arm weakness", "slurred speech",
    "loss of consciousness", "unresponsive", "seizure",
    "suicidal", "self-harm", "anaphylaxis",
    "severe allergic reaction", "choking", "poisoning",
    "overdose", "gunshot", "stabbing",
]

HIGH_RISK_SYMPTOMS = [
    "chest pain", "breathing difficulty", "severe headache",
    "numbness one side", "vision loss", "high fever",
    "blood in vomit", "blood in stool", "severe abdominal pain",
    "confusion", "altered mental status", "neck stiffness",
]


async def handle_triage_assessment(args: dict) -> dict:
    """
    Calculate ESI triage severity based on patient data.
    """
    chief_complaint = args.get("chief_complaint", "").lower()
    symptoms = [s.lower() for s in args.get("symptoms", [])]
    severity_score = args.get("severity_score", 5)
    duration_hours = args.get("duration_hours", 0)
    vital_signs_abnormal = args.get("vital_signs_abnormal", False)
    age = args.get("age")
    has_red_flags = args.get("has_red_flags", False)

    all_text = chief_complaint + " " + " ".join(symptoms)

    # Step 1: Check for ESI Level 1 (Resuscitation)
    level_1_triggers = ["unresponsive", "not breathing", "cardiac arrest", "no pulse", "apneic"]
    if any(trigger in all_text for trigger in level_1_triggers):
        return _build_result(1, chief_complaint, symptoms, severity_score,
                             "IMMEDIATE RESUSCITATION REQUIRED. Call 911.",
                             "emergency")

    # Step 2: Check for ESI Level 2 (Emergent)
    red_flag_count = sum(1 for rf in RED_FLAG_KEYWORDS if rf in all_text)
    high_risk_count = sum(1 for hr in HIGH_RISK_SYMPTOMS if hr in all_text)

    if has_red_flags or red_flag_count >= 2 or (severity_score >= 9 and high_risk_count > 0):
        return _build_result(2, chief_complaint, symptoms, severity_score,
                             "EMERGENT — Proceed to Emergency Department immediately.",
                             "emergency")

    # Step 3: Check for ESI Level 3 (Urgent)
    resources_needed = _estimate_resources(symptoms, severity_score, vital_signs_abnormal)

    if (red_flag_count >= 1 or severity_score >= 7 or vital_signs_abnormal or
            high_risk_count >= 2 or resources_needed >= 2):
        recommendation = "URGENT — Seek medical care within 1-2 hours. Visit urgent care or ER."
        if age and (age > 65 or age < 2):
            recommendation += " Extra caution due to age."
        return _build_result(3, chief_complaint, symptoms, severity_score,
                             recommendation, "urgent_care")

    # Step 4: Check for ESI Level 4 (Less Urgent)
    if severity_score >= 4 or resources_needed == 1 or duration_hours > 48:
        care_type = "telehealth" if severity_score < 6 else "urgent_care"
        return _build_result(4, chief_complaint, symptoms, severity_score,
                             "LESS URGENT — Schedule a medical visit within 24-48 hours. Telehealth may be appropriate.",
                             care_type)

    # Step 5: ESI Level 5 (Non-Urgent)
    return _build_result(5, chief_complaint, symptoms, severity_score,
                         "NON-URGENT — Self-care with monitoring is likely appropriate. Schedule a primary care visit if symptoms persist.",
                         "primary_care")


def _estimate_resources(symptoms: list, severity: int, vitals_abnormal: bool) -> int:
    """Estimate how many healthcare resources the patient might need."""
    resources = 0
    if len(symptoms) > 3:
        resources += 1
    if severity >= 6:
        resources += 1
    if vitals_abnormal:
        resources += 1
    if any("blood" in s for s in symptoms):
        resources += 1
    return resources


def _build_result(esi_level: int, complaint: str, symptoms: list,
                  severity: int, recommendation: str, care_type: str) -> dict:
    """Build a standardized triage result."""
    level_labels = {
        1: "Resuscitation",
        2: "Emergent",
        3: "Urgent",
        4: "Less Urgent",
        5: "Non-Urgent",
    }

    level_colors = {
        1: "red",
        2: "orange",
        3: "yellow",
        4: "green",
        5: "blue",
    }

    return {
        "esi_level": esi_level,
        "esi_label": level_labels[esi_level],
        "severity_color": level_colors[esi_level],
        "patient_severity_score": severity,
        "chief_complaint": complaint,
        "symptom_count": len(symptoms),
        "recommendation": recommendation,
        "recommended_care_type": care_type,
        "timestamp": datetime.now().isoformat(),
        "disclaimer": "This is an AI-assisted triage assessment for informational purposes only. It is NOT a medical diagnosis. Always consult a qualified healthcare professional.",
    }

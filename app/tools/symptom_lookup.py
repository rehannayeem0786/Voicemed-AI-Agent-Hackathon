"""Symptom Lookup Tool — searches medical symptom database."""

import json
import os

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "symptoms.json")


def _load_symptoms():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["symptoms"]


async def handle_symptom_lookup(args: dict) -> dict:
    """
    Search symptom database for matching conditions, red flags, and follow-up questions.
    """
    reported = [s.lower().strip() for s in args.get("symptoms", [])]
    duration = args.get("duration_hours")
    severity = args.get("severity", 5)

    symptoms_db = _load_symptoms()
    matches = []
    all_red_flags = []
    all_conditions = []
    follow_up_questions = []

    for symptom_entry in symptoms_db:
        name = symptom_entry["name"].lower()
        # Fuzzy match — check if any reported symptom is contained in or contains the DB entry
        for rep in reported:
            if rep in name or name in rep or any(word in name for word in rep.split()):
                # Adjust severity based on duration
                adjusted_severity = symptom_entry["severity_base"]
                if duration and duration > 72:
                    adjusted_severity = min(10, adjusted_severity + 2)
                if severity and severity >= 7:
                    adjusted_severity = min(10, max(adjusted_severity, severity))

                matches.append({
                    "symptom": symptom_entry["name"],
                    "category": symptom_entry["category"],
                    "severity_score": adjusted_severity,
                })
                all_red_flags.extend(symptom_entry["red_flags"])
                all_conditions.extend(symptom_entry["associated_conditions"])
                follow_up_questions.extend(symptom_entry["questions"])
                break

    # Determine overall risk level
    max_severity = max((m["severity_score"] for m in matches), default=3)
    if max_severity >= 8:
        risk_level = "HIGH — Recommend immediate medical attention"
    elif max_severity >= 5:
        risk_level = "MODERATE — Recommend medical evaluation within 24 hours"
    else:
        risk_level = "LOW — May be suitable for self-care with monitoring"

    return {
        "matched_symptoms": matches,
        "possible_conditions": list(set(all_conditions))[:8],
        "red_flags_to_screen": list(set(all_red_flags))[:6],
        "suggested_follow_up_questions": list(set(follow_up_questions))[:4],
        "overall_risk_level": risk_level,
        "severity_score": max_severity,
    }

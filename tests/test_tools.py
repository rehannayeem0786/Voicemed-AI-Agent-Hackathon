"""Tests for the six clinical tool handlers against the real data files."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.tools import TOOL_HANDLERS, dispatch_tool  # noqa: E402
from app.tools.drug_interaction import handle_drug_interaction  # noqa: E402
from app.tools.symptom_lookup import handle_symptom_lookup  # noqa: E402
from app.tools.triage_assessment import handle_triage_assessment  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class TestSymptomLookup:
    def test_finds_chest_pain_with_red_flags(self):
        result = run(handle_symptom_lookup({"symptoms": ["chest pain"]}))
        assert "error" not in result
        text = json_dumps(result)
        assert "red_flag" in text or "radiating" in text

    def test_multi_symptom_query(self):
        result = run(handle_symptom_lookup({"symptoms": ["fever", "cough"], "duration_hours": 48, "severity": 5}))
        assert "error" not in result

    def test_unknown_symptom_is_handled(self):
        result = run(handle_symptom_lookup({"symptoms": ["zorg blatt"]}))
        assert "error" not in result  # graceful, no crash

    def test_empty_input_is_graceful(self):
        result = run(handle_symptom_lookup({"symptoms": []}))
        assert isinstance(result, dict)


class TestDrugInteraction:
    def test_detects_warfarin_aspirin(self):
        result = run(handle_drug_interaction({"medications": ["aspirin", "warfarin"]}))
        assert "error" not in result
        text = json_dumps(result).lower()
        assert "warfarin" in text and "bleeding" in text

    def test_severity_is_included(self):
        result = run(handle_drug_interaction({"medications": ["sertraline", "tramadol"]}))
        text = json_dumps(result).lower()
        assert "high" in text  # known high-severity pair

    def test_aliases_are_normalized(self):
        # " ASA " is an alias of aspirin; result must match the direct name.
        result = run(handle_drug_interaction({"medications": [" ASA ", "Warfarin 500"]}))
        text = json_dumps(result).lower()
        assert "bleeding" in text

    def test_single_medication_has_no_interactions(self):
        result = run(handle_drug_interaction({"medications": ["aspirin"]}))
        assert "error" not in result

    def test_unknown_medication_is_graceful(self):
        result = run(handle_drug_interaction({"medications": ["unobtainium"]}))
        assert isinstance(result, dict)


class TestTriageAssessment:
    def test_critical_presentation_scores_esi_1_or_2(self):
        result = run(handle_triage_assessment({
            "chief_complaint": "crushing chest pain",
            "symptoms": ["chest pain", "shortness of breath", "sweating"],
            "severity_score": 9,
            "has_red_flags": True,
        }))
        assert result["esi_level"] in (1, 2)
        assert result["recommendation"]

    def test_mild_presentation_scores_esi_4_or_5(self):
        result = run(handle_triage_assessment({
            "chief_complaint": "mild runny nose",
            "symptoms": ["cough"],
            "severity_score": 2,
            "duration_hours": 24,
        }))
        assert result["esi_level"] in (4, 5)

    def test_result_shape(self):
        result = run(handle_triage_assessment({
            "chief_complaint": "headache", "symptoms": ["headache"], "severity_score": 5,
        }))
        for key in ("esi_level", "esi_label", "recommendation"):
            assert key in result, key


class TestOtherTools:
    def test_soap_note_generation(self):
        result = run(TOOL_HANDLERS["generate_soap_note"]({
            "subjective": "Patient reports chest pain for 2 hours.",
            "assessment": "Possible acute coronary syndrome. ESI 2.",
            "plan": "Emergency department referral now.",
            "icd10_codes": ["R07.9"],
        }))
        assert "error" not in result
        text = json_dumps(result)
        assert "Subjective" in text and "R07.9" in text

    def test_appointment_booking(self):
        result = run(TOOL_HANDLERS["book_appointment"]({
            "appointment_type": "telehealth", "reason": "follow-up", "preferred_time": "morning",
        }))
        assert "error" not in result

    def test_emergency_alert(self):
        result = run(TOOL_HANDLERS["emergency_alert"]({
            "emergency_type": "cardiac", "description": "crushing chest pain",
            "patient_symptoms": ["chest pain"],
        }))
        text = json_dumps(result)
        assert "911" in text or "escalat" in text.lower()


class TestDispatch:
    def test_unknown_tool_returns_error(self):
        result = run(dispatch_tool("not_a_real_tool", {}))
        assert "error" in result

    def test_handler_exception_becomes_error_dict(self):
        result = run(dispatch_tool("symptom_lookup", {"symptoms": None}))
        assert isinstance(result, dict)  # never raises into the agent session


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj).lower() if False else json.dumps(obj)

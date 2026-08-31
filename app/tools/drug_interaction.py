"""Drug Interaction Checker — cross-references medications for dangerous interactions."""

import json
import os
import re

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "drug_interactions.json")


def _load_interactions():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_DOSAGE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)?\b", re.IGNORECASE)


def _normalize_drug(name: str, aliases: dict) -> str:
    """Normalize a drug name to its canonical form using the alias map.

    Voice transcripts often carry a dose with the name ("warfarin 500",
    "metformin 500 mg"), so dosage suffixes are stripped before matching.
    """
    name_lower = name.lower().strip()
    # Check if it's already a canonical name
    if name_lower in aliases:
        return name_lower
    # Check aliases
    for canonical, alias_list in aliases.items():
        if name_lower in [a.lower() for a in alias_list]:
            return canonical
    # Retry without dosage/strength tokens ("warfarin 500" -> "warfarin").
    stripped = _DOSAGE_RE.sub(" ", name_lower).strip(" -,")
    if stripped and stripped != name_lower:
        if stripped in aliases:
            return stripped
        for canonical, alias_list in aliases.items():
            if stripped in [a.lower() for a in alias_list]:
                return canonical
    return name_lower


async def handle_drug_interaction(args: dict) -> dict:
    """
    Check for dangerous interactions between the patient's medications.
    """
    medications = args.get("medications", [])
    if len(medications) < 1:
        return {"interactions_found": False, "message": "No medications provided to check."}

    data = _load_interactions()
    interactions_db = data["interactions"]
    aliases = data["drug_aliases"]

    # Normalize all drug names
    normalized = [_normalize_drug(m, aliases) for m in medications]

    found_interactions = []
    checked_pairs = set()

    for i, drug_a in enumerate(normalized):
        for j, drug_b in enumerate(normalized):
            if i >= j:
                continue
            pair = tuple(sorted([drug_a, drug_b]))
            if pair in checked_pairs:
                continue
            checked_pairs.add(pair)

            # Check interaction database
            for interaction in interactions_db:
                db_a = interaction["drug_a"].lower()
                db_b = interaction["drug_b"].lower()

                if (drug_a == db_a and drug_b == db_b) or (drug_a == db_b and drug_b == db_a):
                    found_interactions.append({
                        "drug_a": medications[i],
                        "drug_b": medications[j],
                        "severity": interaction["severity"],
                        "effect": interaction["effect"],
                        "recommendation": interaction["recommendation"],
                    })

    # Also check for drug class interactions (SSRIs, NSAIDs, etc.)
    ssri_drugs = {"sertraline", "fluoxetine", "paroxetine", "citalopram", "escitalopram"}
    nsaid_drugs = {"ibuprofen", "naproxen", "aspirin", "diclofenac", "meloxicam"}

    patient_ssris = [m for m, n in zip(medications, normalized) if n in ssri_drugs]
    patient_nsaids = [m for m, n in zip(medications, normalized) if n in nsaid_drugs]

    if patient_ssris and patient_nsaids:
        already_found = any(
            i["drug_a"] in patient_ssris and i["drug_b"] in patient_nsaids or
            i["drug_b"] in patient_ssris and i["drug_a"] in patient_nsaids
            for i in found_interactions
        )
        if not already_found:
            found_interactions.append({
                "drug_a": patient_ssris[0],
                "drug_b": patient_nsaids[0],
                "severity": "moderate",
                "effect": "SSRIs combined with NSAIDs increase the risk of gastrointestinal bleeding.",
                "recommendation": "Use the lowest effective NSAID dose for the shortest time. Consider adding a gastroprotective agent.",
            })

    # Determine overall risk
    if any(i["severity"] == "critical" for i in found_interactions):
        overall_risk = "CRITICAL — Dangerous interaction detected. Immediate medical review required."
    elif any(i["severity"] == "high" for i in found_interactions):
        overall_risk = "HIGH — Significant interaction found. Consult your physician."
    elif found_interactions:
        overall_risk = "MODERATE — Interaction detected. Monitor for side effects."
    else:
        overall_risk = "LOW — No known interactions found between these medications."

    return {
        "interactions_found": len(found_interactions) > 0,
        "interaction_count": len(found_interactions),
        "interactions": found_interactions,
        "overall_risk": overall_risk,
        "medications_checked": medications,
    }

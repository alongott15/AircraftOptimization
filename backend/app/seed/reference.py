"""Reference data for the example DB (DATABASE.md §5). Hebrew names are data, not UI copy."""

from __future__ import annotations

AIRCRAFT_TYPES = [
    # type_code, name_he, engines, endurance h, idle limit days, model_shape
    ("SE-100", "מטוס אימון חד-מנועי", 1, 4.5, 30, "SINGLE_PROP_LOW_WING"),
    ("TP-200", "מטוס טורבו-פרופ דו-מנועי", 2, 6.0, 45, "TWIN_TURBOPROP_HIGH_WING"),
    ("JT-300", "מטוס סילון דו-מנועי", 2, 9.0, 45, "TWIN_JET_LOW_WING"),
]

BASES = [("NTH", "בסיס צפון"), ("CEN", "בסיס מרכז"), ("STH", "בסיס דרום")]

ACTIVITY_UNITS = [
    ("FH", "שעות טיסה", "ש״ט", "COUNTER"),
    ("LDG", "נחיתות", "נח׳", "COUNTER"),
    ("TG", "גע-וסע", "ג״ו", "COUNTER"),
    ("CYC", "מחזורים", "מחז׳", "COUNTER"),
    ("ENG_START", "התנעות מנוע", "התנעות", "COUNTER"),
    ("CAL_DAY", "ימים קלנדריים", "ימים", "CALENDAR"),
    ("IDLE_DAY", "ימים ללא טיסה", "ימים", "IDLE"),
]

QUALIFICATIONS = [
    ("IFR", "טיסת מכשירים"),
    ("NIGHT", "טיסת לילה"),
    ("PAX", "הסעת נוסעים"),
    ("CARGO", "הובלת מטען"),
    ("OVERWATER", "טיסה מעל מים"),
    ("MEDEVAC", "פינוי רפואי"),
    ("ICING", "טיסה בתנאי קרח"),
]

TYPE_QUALIFICATIONS = {
    "SE-100": ["IFR", "NIGHT"],
    "TP-200": ["IFR", "NIGHT", "PAX", "CARGO", "OVERWATER", "MEDEVAC", "ICING"],
    "JT-300": ["IFR", "NIGHT", "PAX", "CARGO", "OVERWATER", "ICING"],
}

ZONES = [
    ("NOSE", "חרטום"), ("COCKPIT", "תא טייס"), ("FUSELAGE", "גוף המטוס"), ("WING_L", "כנף שמאל"),
    ("WING_R", "כנף ימין"), ("ENGINE_1", "מנוע 1"), ("ENGINE_2", "מנוע 2"), ("GEAR_NOSE", "כן נסע קדמי"),
    ("GEAR_MAIN", "כן נסע ראשי"), ("TAIL", "זנב"),
]

ATA_ZONE_MAP = [
    (21, 26, "FUSELAGE"), (27, 27, "WING_L"), (28, 28, "WING_R"), (30, 30, "WING_L"), (31, 31, "NOSE"),
    (32, 32, "GEAR_MAIN"), (33, 33, "FUSELAGE"), (34, 34, "NOSE"), (35, 38, "FUSELAGE"), (52, 53, "FUSELAGE"),
    (55, 55, "TAIL"), (57, 57, "WING_L"), (61, 61, "ENGINE_1"), (71, 80, "ENGINE_1"),
]

INSPECTION_TYPES = [
    # code, name_he, zone
    ("I50H", "בדיקת 50 שעות", None),
    ("I100H", "בדיקת 100 שעות", None),
    ("A_CHECK", "בדיקת A", None),
    ("C_CHECK", "בדיקת C", None),
    ("ANNUAL", "בדיקה שנתית", None),
    ("LG_INSP", "בדיקת כני נסע", "GEAR_MAIN"),
    ("ENG_BORESCOPE", "בדיקת בורוסקופ למנוע", "ENGINE_1"),
    ("IDLE_CHECK", "בדיקה לאחר השבתה", None),
]

# inspection, type, unit, interval, tolerance, tg_weight
INSPECTION_LIMITS = [
    ("I50H", "SE-100", "FH", 50, 5, 1.0),
    ("I100H", "SE-100", "FH", 100, 10, 1.0),
    ("I100H", "TP-200", "FH", 100, 10, 1.0),
    ("A_CHECK", "TP-200", "FH", 500, 0, 1.0),
    ("A_CHECK", "TP-200", "CAL_DAY", 120, 0, 1.0),
    ("A_CHECK", "JT-300", "FH", 500, 0, 1.0),
    ("A_CHECK", "JT-300", "CAL_DAY", 120, 0, 1.0),
    ("C_CHECK", "TP-200", "FH", 4000, 0, 1.0),
    ("C_CHECK", "TP-200", "CAL_DAY", 730, 0, 1.0),
    ("C_CHECK", "JT-300", "FH", 4000, 0, 1.0),
    ("C_CHECK", "JT-300", "CAL_DAY", 730, 0, 1.0),
    ("ANNUAL", "SE-100", "CAL_DAY", 365, 0, 1.0),
    ("ANNUAL", "TP-200", "CAL_DAY", 365, 0, 1.0),
    ("ANNUAL", "JT-300", "CAL_DAY", 365, 0, 1.0),
    ("LG_INSP", "SE-100", "LDG", 300, 0, 1.0),
    ("LG_INSP", "TP-200", "LDG", 600, 0, 1.0),
    ("LG_INSP", "JT-300", "LDG", 800, 0, 0.5),
    ("ENG_BORESCOPE", "TP-200", "FH", 300, 0, 1.0),
    ("ENG_BORESCOPE", "TP-200", "ENG_START", 600, 0, 1.0),
    ("ENG_BORESCOPE", "JT-300", "FH", 300, 0, 1.0),
    ("ENG_BORESCOPE", "JT-300", "ENG_START", 600, 0, 1.0),
    ("IDLE_CHECK", "SE-100", "IDLE_DAY", 30, 0, 1.0),
    ("IDLE_CHECK", "TP-200", "IDLE_DAY", 45, 0, 1.0),
    ("IDLE_CHECK", "JT-300", "IDLE_DAY", 45, 0, 1.0),
]

# Major inspection per type (for "FH since last major" ML feature and hazard).
MAJOR_INSPECTION = {"SE-100": ("I100H", 100.0), "TP-200": ("A_CHECK", 500.0), "JT-300": ("A_CHECK", 500.0)}

PERMIT_TYPES = [
    # code, name_he, is_grounding, is_required
    ("COA", "תעודת כושר טיסה", True, True),
    ("REG", "תעודת רישום", True, True),
    ("RADIO", "רישיון תחנת רדיו", True, True),
    ("INSURANCE", "פוליסת ביטוח", True, True),
    ("SPECIAL_FLIGHT", "היתר טיסה מיוחד", True, False),
]

# Synthetic defect catalogue: ATA, zone, title (Hebrew MRO free text), twin-only
DEFECT_CATALOGUE = [
    (32, "GEAR_MAIN", "שחיקת צמיג ראשי", False),
    (32, "GEAR_NOSE", "רעידות בכן הנסע הקדמי", False),
    (79, "ENGINE_1", "לחץ שמן נמוך במנוע מס׳ 1", False),
    (79, "ENGINE_2", "לחץ שמן נמוך במנוע מס׳ 2", True),
    (34, "NOSE", "תקלה במערכת הניווט", False),
    (27, "WING_L", "חופש תנועה חריג במאזנת שמאל", False),
    (27, "WING_R", "חופש תנועה חריג במאזנת ימין", False),
    (28, "WING_R", "דליפת דלק קלה בכנף ימין", False),
    (21, "FUSELAGE", "תקלה במערכת מיזוג האוויר", False),
    (33, "FUSELAGE", "נורית תאורה פנימית אינה פועלת", False),
    (55, "TAIL", "סדק בצבע במייצב האופקי", False),
    (31, "COCKPIT", "מחוון גובה מציג סטייה", False),
    (61, "ENGINE_1", "רעידות במדחף", False),
    (52, "FUSELAGE", "נעילת דלת נוסעים קשה", False),
]

WEIGHT_PRESET_ROWS = ["BALANCED", "MAX_SAFETY", "MAX_AVAILABILITY"]
